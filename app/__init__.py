"""Application factory."""
import logging
import os

from flask import Flask, jsonify, request
from flask_login import current_user
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import BASE_DIR, CONFIG_BY_NAME
from .extensions import csrf, db, limiter, login_manager, migrate

MIN_PRODUCTION_SECRET_LENGTH = 32
REQUIRED_DRIVER = "postgresql+psycopg"
MIGRATIONS_DIR = os.path.join(BASE_DIR, "migrations")


def create_app(config_name=None):
    """Build and return a configured Flask app.

    config_name: "development", "testing" or "production".
    Falls back to the FLASK_CONFIG environment variable, then "development".
    """
    name = (config_name or os.environ.get("FLASK_CONFIG") or "development").strip().lower()
    if name not in CONFIG_BY_NAME:
        raise ValueError(f"Unknown config {name!r}. Use one of: {', '.join(CONFIG_BY_NAME)}")

    app = Flask(__name__)
    app.config.from_object(CONFIG_BY_NAME[name])
    app.config["CONFIG_NAME"] = name

    _validate_config(app)
    _configure_logging(app)
    _configure_proxy(app)
    _init_extensions(app)
    _register_auth_handlers()
    _register_error_handlers(app)
    _register_security_headers(app)
    _register_blueprints(app)
    _register_template_filters(app)
    _register_core_routes(app)
    _register_cli(app)
    return app


def _validate_config(app):
    """Fail at startup with a clear message instead of failing mid-class."""
    secret = app.config.get("SECRET_KEY")
    if not secret:
        raise RuntimeError("SECRET_KEY is not set. Add it to your .env file.")
    if app.config["CONFIG_NAME"] == "production" and len(secret) < MIN_PRODUCTION_SECRET_LENGTH:
        raise RuntimeError(
            f"SECRET_KEY must be at least {MIN_PRODUCTION_SECRET_LENGTH} characters in production."
        )

    uri = app.config.get("SQLALCHEMY_DATABASE_URI")
    if not uri:
        var = "TEST_DATABASE_URL" if app.config.get("TESTING") else "DATABASE_URL"
        raise RuntimeError(f"{var} is not set. Add it to your .env file.")

    try:
        url = make_url(uri)
    except ArgumentError as exc:
        raise RuntimeError(
            "Database URL is malformed. If the password contains @ : / # or %, URL-encode it."
        ) from exc

    if url.drivername != REQUIRED_DRIVER:
        raise RuntimeError(f"Database URL must start with {REQUIRED_DRIVER}://")

    _check_database_separation(app, url)


def _check_database_separation(app, url):
    """Keep test and real data apart, in both directions.

    Tests wipe every table, so:
    - tests may only use a database whose name ends in _test, and never the
      same database as DATABASE_URL;
    - the real app may never use a database whose name ends in _test, because
      the next pytest run would silently erase it.
    """
    is_test_db = (url.database or "").endswith("_test")

    if not app.config.get("TESTING"):
        if is_test_db:
            raise RuntimeError(
                f"DATABASE_URL points to '{url.database}', which is the TEST database. "
                "Tests erase it on every run. Point DATABASE_URL at attendance_dev instead."
            )
        return

    if not is_test_db:
        raise RuntimeError(
            "Refusing to run tests: the test database name must end with '_test' "
            "because tests delete all data in it."
        )

    dev_uri = os.environ.get("DATABASE_URL")
    if dev_uri:
        try:
            dev = make_url(dev_uri)
        except ArgumentError:
            return  # the dev app itself will report the malformed URL
        same_server = (dev.host or "localhost", dev.port or 5432) == (url.host or "localhost", url.port or 5432)
        if same_server and dev.database == url.database:
            raise RuntimeError(
                "Refusing to run tests: TEST_DATABASE_URL and DATABASE_URL point to the same "
                "database. Tests would erase your development data."
            )


def _configure_logging(app):
    app.logger.setLevel(logging.DEBUG if app.debug else logging.INFO)


def _configure_proxy(app):
    """Trust X-Forwarded-* headers only from the configured number of proxies.

    Without this, behind ngrok/cloudflared every request appears to come from
    the proxy's IP, and request.is_secure is wrong.
    """
    count = app.config["TRUSTED_PROXY_COUNT"]
    if count < 0:
        raise RuntimeError("TRUSTED_PROXY_COUNT cannot be negative.")
    if count:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=count, x_proto=count, x_host=count)


def _init_extensions(app):
    # Importing models registers every table and the Flask-Login user loader.
    from . import models  # noqa: F401

    db.init_app(app)
    # Absolute path, so `flask db ...` and the tests find migrations from any folder.
    migrate.init_app(app, db, directory=MIGRATIONS_DIR)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)


def _register_auth_handlers():
    from .utils.decorators import handle_unauthorized

    login_manager.unauthorized_handler(handle_unauthorized)


def _register_error_handlers(app):
    from .utils.errors import register_error_handlers

    register_error_handlers(app)


def _register_blueprints(app):
    from .blueprints.admin_api import bp as admin_api_bp
    from .blueprints.auth import bp as auth_bp
    from .blueprints.face import api_bp as face_api_bp
    from .blueprints.face import bp as face_bp
    from .blueprints.faculty import api_bp as faculty_api_bp
    from .blueprints.faculty import bp as faculty_bp
    from .blueprints.main import bp as main_bp
    from .blueprints.student import bp as student_bp

    for blueprint in (auth_bp, main_bp, student_bp, faculty_bp, faculty_api_bp, face_bp, face_api_bp, admin_api_bp):
        app.register_blueprint(blueprint)
    # Token-only API: no cookies are used, so CSRF does not apply (see admin_api/__init__.py).
    csrf.exempt(admin_api_bp)


def _register_security_headers(app):
    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        # Only this site may use the camera; nothing may use the microphone or location.
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        # Pages for logged-in users must not be cached: otherwise pressing Back
        # after logging out on a shared computer would show the last page.
        # (Browsers' in-memory back-forward cache is handled by page-freshness.js
        # and the Clear-Site-Data header on logout.)
        # API responses (temporary passwords, rosters) must never be cached either.
        private = current_user.is_authenticated or request.path.startswith("/api/")
        if private and not response.headers.get("Cache-Control"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"  # older browsers and proxies
            response.headers["Expires"] = "0"
        return response


def _register_template_filters(app):
    from .utils.timeutils import to_local

    @app.template_filter("localtime")
    def localtime(moment, fmt="%H:%M"):
        return to_local(moment).strftime(fmt) if moment else ""


def _register_cli(app):
    from .cli import register_cli

    register_cli(app)


def _register_core_routes(app):
    @app.get("/health")
    @limiter.exempt
    def health():
        """Liveness + database check. Exposes no internal details."""
        try:
            db.session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            app.logger.exception("Health check: database unreachable")
            return jsonify(status="degraded", database="unreachable"), 503
        return jsonify(status="ok", database="ok"), 200
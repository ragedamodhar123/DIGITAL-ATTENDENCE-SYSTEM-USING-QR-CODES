"""Application factory."""
import logging
import os

from flask import Flask, jsonify
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

    # Tests wipe every table. Never let them touch the development database.
    if app.config.get("TESTING") and not (url.database or "").endswith("_test"):
        raise RuntimeError(
            "Refusing to run tests: the test database name must end with '_test' "
            "because tests delete all data in it."
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
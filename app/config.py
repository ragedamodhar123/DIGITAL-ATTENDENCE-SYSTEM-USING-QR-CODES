"""Application configuration.

Every secret and environment-specific value comes from environment variables
(the .env file in development). Nothing secret is hard-coded here.
"""
import os
from datetime import timedelta

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _int_env(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY")

    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # pre_ping: silently replaces connections Postgres has dropped (e.g. after laptop sleep).
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 1800}

    # Cookies: not readable by JavaScript, not sent on cross-site POSTs.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = False
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)

    # CSRF tokens live as long as the login session, so a faculty page left
    # open through a long class does not fail with "CSRF token expired".
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None

    # Largest accepted request body (face images are well under this).
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024

    # Rate limiting. In-memory storage is fine for a single waitress process.
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True

    TRUSTED_PROXY_COUNT = _int_env("TRUSTED_PROXY_COUNT", 0)

    # Attendance rules (used from Module 4 onward)
    APP_TIMEZONE = "Asia/Kolkata"
    QR_ROTATION_SECONDS = 10
    QR_GRACE_WINDOWS = 1
    PENDING_CHECKIN_SECONDS = 60
    SESSION_MAX_MINUTES = 120


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class TestingConfig(BaseConfig):
    TESTING = True
    SECRET_KEY = "test-secret-key-only-for-automated-tests"
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL")
    # Disabled for most tests; a dedicated test re-enables it to prove CSRF works.
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False


class ProductionConfig(BaseConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"


CONFIG_BY_NAME = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}

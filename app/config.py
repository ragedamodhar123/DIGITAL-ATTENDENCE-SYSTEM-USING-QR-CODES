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
    # Applied to the login form in Module 1, part 3 (see app/utils/ratelimit.py).
    RATELIMIT_LOGIN_PER_USER = "10 per 5 minutes"   # one username from one IP
    RATELIMIT_LOGIN_PER_IP = "300 per 5 minutes"    # whole classroom behind one college IP

    TRUSTED_PROXY_COUNT = _int_env("TRUSTED_PROXY_COUNT", 0)

    # Face checks (Module 3). Calibrated on the official OpenCV models; see README "Face checks".
    FACE_MODEL_DIR = os.environ.get("FACE_MODEL_DIR", os.path.join(BASE_DIR, "models"))
    FACE_MATCH_THRESHOLD = 0.363          # cosine similarity; OpenCV's published value for SFace
    FACE_MIN_DETECTION_SCORE = 0.85       # how sure the detector must be that it found a face
    FACE_MIN_SIZE_PX = 80                 # face width/height in the (scaled) photo
    FACE_EDGE_TOLERANCE_PX = 4            # how far a face box may stick out of the photo
    FACE_MIN_SHARPNESS = 25.0             # blur check, measured on the face resized to 112 px
    FACE_BRIGHTNESS_RANGE = (40.0, 225.0)  # mean face brightness, 0-255
    FACE_ENROL_CAPTURES = 3               # photos taken when registering
    FACE_MAX_IMAGE_BYTES = 3 * 1024 * 1024
    FACE_MAX_IMAGE_SIDE = 4096
    FACE_MIN_IMAGE_SIDE = 160

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
    # Rate limits stay ON in tests (counters are reset before every test), so the
    # tests exercise the same limits as the real app.
    RATELIMIT_STORAGE_URI = "memory://"


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
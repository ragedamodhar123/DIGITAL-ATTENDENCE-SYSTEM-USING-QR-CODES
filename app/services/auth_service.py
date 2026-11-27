"""Login, password change, password rules and admin API-token checks.

Rules
- Login errors never reveal whether the username exists, the password was
  wrong, or the account is disabled: the message is always the same.
- Unknown usernames still run a full password hash check, so response time
  does not reveal which usernames exist.
- Admins cannot log in with a password; they use their API token.
- Changing a password logs the user out everywhere (session_version + 1).
"""
from functools import lru_cache

from ..extensions import db
from ..models import User
from ..utils.security import hash_api_token, hash_password, password_needs_rehash, verify_password
from ..utils.timeutils import utcnow
from ..utils.validators import normalize_username
from .errors import AuthenticationFailed, ValidationFailed

LOGIN_FAILED_MESSAGE = "Invalid username or password."

MAX_USERNAME_LENGTH = 64
PASSWORD_MIN_LENGTH = 8
# Upper bound keeps hashing time predictable (very long inputs are a cheap DoS).
PASSWORD_MAX_LENGTH = 128
MAX_AUTH_HEADER_LENGTH = 200

# Passwords students and staff are most likely to reuse. Compared case-insensitively.
COMMON_PASSWORDS = frozenset(
    {
        "password", "password1", "password123", "passw0rd", "12345678", "123456789",
        "1234567890", "qwerty123", "abc12345", "welcome1", "welcome123", "admin123",
        "letmein1", "iloveyou1", "student1", "student123", "college1", "college123",
        "changeme1", "demo@12345",
    }
)


@lru_cache(maxsize=1)
def _dummy_hash():
    """A real hash to check against when the username does not exist (timing safety)."""
    return hash_password("not-a-real-password-for-timing-only")


# ---- Login ---------------------------------------------------------------

def authenticate(username, password):
    """Return the User for a correct username + password, else raise AuthenticationFailed.

    Does not log the user in (the route calls Flask-Login). Updates last_login_at,
    and upgrades the stored hash if it uses outdated settings.
    """
    if not isinstance(username, str) or not isinstance(password, str):
        _burn_time_and_fail()
    username = normalize_username(username)
    if not username or len(username) > MAX_USERNAME_LENGTH or not password or len(password) > PASSWORD_MAX_LENGTH:
        _burn_time_and_fail()

    user = User.query.filter_by(username=username).one_or_none()
    if user is None:
        _burn_time_and_fail()

    password_ok = verify_password(user.password_hash, password)
    if not password_ok or not user.is_active or user.is_admin:
        raise AuthenticationFailed(LOGIN_FAILED_MESSAGE)

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = utcnow()
    db.session.commit()
    return user


def _burn_time_and_fail():
    verify_password(_dummy_hash(), "wrong-on-purpose")
    raise AuthenticationFailed(LOGIN_FAILED_MESSAGE)


# ---- Password rules ------------------------------------------------------

def password_problems(new_password, *, username=None):
    """Return a list of human-readable problems; empty list means the password is acceptable."""
    if not isinstance(new_password, str) or new_password == "":
        return ["Enter a new password."]

    problems = []
    if len(new_password) < PASSWORD_MIN_LENGTH:
        problems.append(f"Use at least {PASSWORD_MIN_LENGTH} characters.")
    if len(new_password) > PASSWORD_MAX_LENGTH:
        problems.append(f"Use at most {PASSWORD_MAX_LENGTH} characters.")
    if not any(ch.isalpha() for ch in new_password):
        problems.append("Include at least one letter.")
    if not any(ch.isdigit() for ch in new_password):
        problems.append("Include at least one number.")
    if new_password.strip() != new_password:
        problems.append("Do not start or end with a space.")
    if username and username.lower() in new_password.lower():
        problems.append("Do not include your username.")
    if new_password.lower() in COMMON_PASSWORDS:
        problems.append("This password is too common. Choose something less predictable.")
    return problems


# ---- Password change -----------------------------------------------------

def change_password(user, current_password, new_password, confirm_password):
    """Change the user's password after checking every rule.

    Nothing is saved unless all checks pass. On success:
    - must_change_password becomes False (this is how first login is completed),
    - session_version goes up, so every existing login (other phones, the friend
      who knew the temporary password) is signed out.
    The caller must log the user in again with the new session_version.
    """
    errors = {}

    if not isinstance(current_password, str) or not verify_password(user.password_hash, current_password):
        errors["current_password"] = ["Current password is incorrect."]

    problems = password_problems(new_password, username=user.username)
    if not problems and isinstance(current_password, str) and new_password == current_password:
        problems.append("New password must be different from the current one.")
    if problems:
        errors["new_password"] = problems

    if new_password != confirm_password:
        errors["confirm_password"] = ["Passwords do not match."]

    if errors:
        raise ValidationFailed("Password was not changed.", details=errors)

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.password_changed_at = utcnow()
    user.session_version = user.session_version + 1
    db.session.commit()
    return user


# ---- Admin API token -----------------------------------------------------

def admin_from_auth_header(header_value):
    """Return the active admin for an 'Authorization: Bearer <token>' header, else None.

    Lookup is by SHA-256 of the token. Because the database compares hashes of a
    random 256-bit token, response timing cannot be used to guess it.
    """
    if not isinstance(header_value, str) or len(header_value) > MAX_AUTH_HEADER_LENGTH:
        return None
    parts = header_value.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None

    token_hash = hash_api_token(parts[1])
    admin = User.query.filter_by(api_token_hash=token_hash).one_or_none()
    if admin is None or not admin.is_admin or not admin.is_active:
        return None
    return admin
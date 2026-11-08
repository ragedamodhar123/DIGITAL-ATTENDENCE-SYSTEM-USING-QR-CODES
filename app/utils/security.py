"""Password and API-token hashing.

This is the only file that knows which hashing algorithms are used, so
changing an algorithm later means changing one file.
"""
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

API_TOKEN_BYTES = 32  # 256 bits of randomness; impossible to guess


def hash_password(plain):
    """Return an Argon2 hash. Each call gives a different hash (random salt)."""
    if not isinstance(plain, str) or plain == "":
        raise ValueError("Password must be a non-empty string.")
    return _hasher.hash(plain)


def verify_password(password_hash, plain):
    """True only if `plain` matches. Never raises on bad or corrupted input."""
    if not password_hash or not isinstance(plain, str) or plain == "":
        return False
    try:
        return _hasher.verify(password_hash, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash):
    """True when the hash uses older/weaker settings and should be upgraded at next login."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True


def generate_api_token():
    """Return (token, token_hash). Show the token once; store only the hash."""
    token = secrets.token_urlsafe(API_TOKEN_BYTES)
    return token, hash_api_token(token)


def hash_api_token(token):
    """SHA-256 of the token.

    Why not Argon2 here: we must find the admin BY the token on every request,
    which needs the same input to always give the same hash. A 256-bit random
    token cannot be brute-forced, so a fast hash is safe for it (unlike passwords).
    """
    if not isinstance(token, str) or token == "":
        raise ValueError("Token must be a non-empty string.")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

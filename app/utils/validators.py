"""Small input rules shared by the CLI now and the API schemas later (Module 2)."""
import re

# Lowercase letters, digits, hyphen, underscore. Must start with a letter or digit.
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")


def normalize_username(raw):
    """Return the stored form of a username: trimmed and lowercase."""
    return raw.strip().lower() if isinstance(raw, str) else raw


def is_valid_username(raw):
    value = normalize_username(raw)
    return isinstance(value, str) and bool(USERNAME_PATTERN.fullmatch(value))


def is_blank(value):
    return not isinstance(value, str) or value.strip() == ""
"""Shared validation pieces.

Schemas check the SHAPE of input (types, lengths, formats, unknown fields).
Services check the RULES (does the section exist, is the username taken,
is this person allowed to do this).
"""
import re

from marshmallow import EXCLUDE, RAISE, Schema, ValidationError, fields, pre_load, validate

from ..services.errors import ValidationFailed
from ..utils.validators import USERNAME_PATTERN, normalize_username

USERNAME_HELP = "Use 3-64 characters: letters, digits, - or _, starting with a letter or digit."


class StrictSchema(Schema):
    """Rejects unknown fields, so a typo like "fullname" is reported, not silently ignored."""

    class Meta:
        unknown = RAISE

    @pre_load
    def _strip_strings(self, data, **kwargs):
        if isinstance(data, dict):
            return {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}
        return data


class QuerySchema(Schema):
    """For URL query strings, which often carry extra parameters (cache busters etc.)."""

    class Meta:
        unknown = EXCLUDE


class Username(fields.String):
    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        value = normalize_username(value)
        if not USERNAME_PATTERN.fullmatch(value):
            raise ValidationError(USERNAME_HELP)
        return value


class OptionalEmail(fields.String):
    """Email that may be omitted, null or blank (stored as NULL), always lowercase.

    Built on String (not fields.Email) because Email's built-in check would also
    run on the None produced for a blank email and reject it.
    """

    MAX_LENGTH = 254
    _check_email = validate.Email(error="Enter a valid email address.")

    def __init__(self, **kwargs):
        super().__init__(allow_none=True, **kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return None
        value = super()._deserialize(value, attr, data, **kwargs).strip()
        if len(value) > self.MAX_LENGTH:
            raise ValidationError(f"Use at most {self.MAX_LENGTH} characters.")
        return self._check_email(value).lower()


def full_name_field(required=True):
    return fields.String(required=required, validate=validate.Length(min=1, max=120))


def code_field(required=True, max_length=32):
    """Section / course codes: stored uppercase."""
    return fields.Function(
        deserialize=lambda v: _code(v, max_length),
        required=required,
    )


CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]*$")


def _code(value, max_length):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Enter a code.")
    value = value.strip().upper()
    if len(value) > max_length:
        raise ValidationError(f"Use at most {max_length} characters.")
    if not CODE_PATTERN.fullmatch(value):
        raise ValidationError("Use letters, digits, - or _, starting with a letter or digit.")
    return value


def load_or_fail(schema, data):
    """Validate input with a schema; raise ValidationFailed with per-field messages."""
    if data is None:
        raise ValidationFailed("Send a JSON body.", details={"_body": ["Request body must be JSON."]})
    try:
        return schema.load(data)
    except ValidationError as exc:
        raise ValidationFailed("Some fields are invalid.", details=_flatten(exc.messages)) from exc


def _flatten(messages):
    """Marshmallow can nest messages; keep them JSON-friendly and predictable."""
    if isinstance(messages, list):
        return {"_body": [str(m) for m in messages]}
    return messages
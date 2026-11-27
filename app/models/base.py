"""Shared column helpers for all models."""
from sqlalchemy import func

from ..extensions import db


class TimestampMixin:
    """created_at / updated_at, stored in UTC, filled in by PostgreSQL itself.

    server_default=now() means even rows inserted from pgAdmin or a script get
    a correct timestamp, not just rows inserted through the app.
    """

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


def clean_str(value):
    """Strip surrounding spaces; leave non-strings (e.g. None) untouched."""
    return value.strip() if isinstance(value, str) else value

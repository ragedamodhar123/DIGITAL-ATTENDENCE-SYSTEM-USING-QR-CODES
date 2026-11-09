"""Helpers shared by several services."""
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from .errors import Conflict, PermissionDenied


def require_admin(actor):
    if actor is None or not actor.is_admin or not actor.is_active:
        raise PermissionDenied("Only the admin can do this.")


def valid_id(value):
    """True for a positive int. bool is a subclass of int; True must not mean id 1."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def commit_or_conflict(unique_fields):
    """Commit; turn a unique-constraint race into a readable 409 error.

    unique_fields maps constraint name -> (field, message), e.g.
    {"uq_sections_code": ("code", "This code is already used.")}
    """
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        text = str(exc.orig)
        for constraint, (field, message) in unique_fields.items():
            if constraint in text:
                raise Conflict(message, details={field: [message]}) from exc
        raise


def escape_like(text):
    """Make % and _ literal in an ILIKE search."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
from flask import jsonify

from ..services.db_service import database_is_up
from . import bp


@bp.get("/health")
def health():
    """Used by you, Postman and later the server monitor. Never shows error details."""
    if database_is_up():
        return jsonify(status="ok", database="ok"), 200
    return jsonify(status="error", database="unreachable"), 503

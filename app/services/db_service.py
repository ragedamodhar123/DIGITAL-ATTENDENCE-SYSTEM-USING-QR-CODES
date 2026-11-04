"""Small database checks used by /health and `flask check-db`."""
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db


def database_info() -> dict:
    """Return server facts. Raises SQLAlchemyError if the database is unreachable."""
    row = db.session.execute(
        text("SELECT current_database(), current_user, current_setting('server_version_num')::int")
    ).one()
    return {"database": row[0], "user": row[1], "server_version_num": row[2]}


def database_is_up() -> bool:
    try:
        db.session.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        db.session.rollback()
        return False

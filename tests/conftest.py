"""Shared test fixtures.

The test database is built by running the real migrations (not create_all),
so every test also proves the migrations produce a correct schema.
Tables are emptied after every test, so tests never depend on each other.
"""
import pytest
from flask_migrate import upgrade
from sqlalchemy import text

from app import create_app
from app.extensions import db


def reset_database():
    """Remove every table, function and trigger owned by the app's DB user.

    Safe only because create_app("testing") refuses databases not ending in _test.
    DROP OWNED works on every PostgreSQL version without superuser rights.
    """
    db.session.remove()
    with db.engine.begin() as conn:
        conn.execute(text("DROP OWNED BY CURRENT_USER"))


@pytest.fixture(scope="session")
def app():
    app = create_app("testing")
    with app.app_context():
        reset_database()
        upgrade()
        yield app
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def cli(app):
    return app.test_cli_runner()


@pytest.fixture(autouse=True)
def _clean_tables(app):
    yield
    db.session.rollback()
    # TRUNCATE instead of DELETE: the audit table blocks DELETE by design
    # (append-only trigger), and TRUNCATE is faster anyway.
    tables = ", ".join(f'"{t.name}"' for t in db.metadata.sorted_tables)
    db.session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    db.session.commit()
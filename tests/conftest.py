"""Shared test fixtures.

The test database is built by running the real migrations (not create_all),
so every test also proves the migrations produce a correct schema.
Tables are emptied after every test, so tests never depend on each other.
"""
import pytest
from flask_migrate import upgrade
from sqlalchemy import text

from app import create_app
from app.extensions import db, limiter


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


@pytest.fixture(scope="session")
def web_app(app):
    """A second app instance used only for HTTP requests.

    The `app` fixture keeps one app context open for the whole run (so tests can
    use the database directly). Flask would let test requests reuse that context,
    leaking the logged-in user from one request into the next, which cannot happen
    on a real server. Requests to this separate instance always get a fresh
    context, exactly like production. Data the test creates must be committed.
    """
    return create_app("testing")


@pytest.fixture
def client(web_app):
    return web_app.test_client()


@pytest.fixture
def cli(app):
    return app.test_cli_runner()


@pytest.fixture(autouse=True)
def _clean_tables(app):
    limiter.reset()  # every test starts with fresh rate-limit counters
    yield
    db.session.rollback()
    # TRUNCATE instead of DELETE: the audit table blocks DELETE by design
    # (append-only trigger), and TRUNCATE is faster anyway.
    tables = ", ".join(f'"{t.name}"' for t in db.metadata.sorted_tables)
    db.session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    db.session.commit()
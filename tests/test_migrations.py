"""Module 0, part 4: migrations match the models and can be undone."""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text

from app.extensions import db


def _schema_differences():
    with db.engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        return compare_metadata(context, db.metadata)


def test_migrated_schema_matches_models():
    # If someone edits a model and forgets `flask db migrate`, this fails.
    assert _schema_differences() == []


def test_database_is_at_latest_revision():
    version = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "0002_account_events"


def test_audit_trigger_exists_after_migration():
    found = db.session.execute(
        text(
            "SELECT 1 FROM pg_trigger WHERE tgname = 'trg_attendance_audit_append_only' "
            "AND tgrelid = 'attendance_audit'::regclass"
        )
    ).scalar()
    assert found == 1


def test_account_events_trigger_exists_after_migration():
    found = db.session.execute(
        text(
            "SELECT 1 FROM pg_trigger WHERE tgname = 'trg_account_events_append_only' "
            "AND tgrelid = 'account_events'::regclass"
        )
    ).scalar()
    assert found == 1


def test_one_open_session_index_is_partial():
    indexdef = db.session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_attendance_sessions_one_open_per_course'")
    ).scalar_one()
    assert "UNIQUE" in indexdef and "WHERE" in indexdef


def test_downgrade_removes_everything_and_upgrade_restores_it():
    db.session.remove()  # release connections so tables can be dropped
    downgrade(revision="base")
    remaining = set(inspect(db.engine).get_table_names()) - {"alembic_version"}
    assert remaining == set()
    functions_left = db.session.execute(
        text("SELECT proname FROM pg_proc WHERE proname IN ('attendance_audit_append_only', 'account_events_append_only')")
    ).all()
    assert functions_left == []
    db.session.remove()

    upgrade()
    assert _schema_differences() == []
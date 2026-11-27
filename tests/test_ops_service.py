"""Module 7, part 2: readiness checks, log files, backups and restore, the waitress server."""
import logging
import os

import pytest
from sqlalchemy import text

from app import create_app
from app.config import ProductionConfig, TestingConfig
from app.extensions import db
from app.models import Section
from app.services import ops_service
from app.services.ops_service import FAIL, OK, WARN, BackupError



def _by_name(results):
    return {r.name: r for r in results}


# ---- readiness ---------------------------------------------------------------

def test_readiness_lists_every_check(app):
    results = _by_name(ops_service.readiness())
    assert set(results) == {"Settings", "Database", "Database schema", "Face models", "Browser libraries",
                            "Host and proxy", "Time zone", "Backup tools"}
    assert results["Settings"].status == WARN            # testing settings, not production
    assert results["Database"].status == OK
    assert results["Database schema"].status == OK and "0003_liveness_challenge" in results["Database schema"].message


def test_schema_behind_is_a_failure(app):
    db.session.execute(text("UPDATE alembic_version SET version_num = '0002_account_events'"))
    try:
        result = _by_name(ops_service.readiness())["Database schema"]
    finally:
        db.session.rollback()
    assert result.status == FAIL and "db upgrade" in result.message


def test_missing_models_and_browser_files_fail(app, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "FACE_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(app, "root_path", str(tmp_path))
    results = _by_name(ops_service.readiness())
    assert results["Face models"].status == FAIL and "download-face-models" in results["Face models"].message
    assert results["Browser libraries"].status == FAIL and "setup step 6" in results["Browser libraries"].message


def test_a_crashing_check_is_reported_not_raised(app, monkeypatch):
    def broken():
        raise RuntimeError("boom")
    monkeypatch.setattr(ops_service, "CHECKS", [("Broken", broken)])
    (result,) = ops_service.readiness()
    assert result.status == FAIL and "boom" in result.message


def test_production_host_warnings(app, monkeypatch):
    monkeypatch.setitem(app.config, "CONFIG_NAME", "production")
    monkeypatch.setitem(app.config, "TRUSTED_HOSTS", None)
    monkeypatch.setitem(app.config, "TRUSTED_PROXY_COUNT", 0)
    result = _by_name(ops_service.readiness())["Host and proxy"]
    assert result.status == WARN and "TRUSTED_HOSTS" in result.message and "TRUSTED_PROXY_COUNT" in result.message


def test_check_command_exit_code(cli, monkeypatch):
    monkeypatch.setattr(ops_service, "CHECKS", [("Good", lambda: (OK, "fine")), ("Meh", lambda: (WARN, "hmm"))])
    result = cli.invoke(args=["check"])
    assert result.exit_code == 0 and "[OK  ] Good: fine" in result.output and "1 warning" in result.output
    monkeypatch.setattr(ops_service, "CHECKS", [("Bad", lambda: (FAIL, "broken"))])
    result = cli.invoke(args=["check"])
    assert result.exit_code == 1 and "must be fixed" in result.output


# ---- log files ---------------------------------------------------------------

def test_log_file_written_and_rotating(tmp_path, monkeypatch):
    monkeypatch.setattr(TestingConfig, "LOG_DIR", str(tmp_path))
    app = create_app("testing")
    try:
        app.logger.warning("hello log file")
        mine = lambda h: getattr(h, "baseFilename", "").startswith(str(tmp_path))  # noqa: E731
        handlers = [h for h in app.logger.handlers if mine(h)]
        assert len(handlers) == 1
        assert (handlers[0].maxBytes, handlers[0].backupCount) == (5 * 1024 * 1024, 10)
        create_app("testing")  # a second app in the same process must not add a second handler
        assert len([h for h in app.logger.handlers if mine(h)]) == 1
        handlers[0].flush()
        content = (tmp_path / "attendance.log").read_text(encoding="utf-8")
        assert "WARNING [app] hello log file" in content
    finally:
        for name in ("app", "waitress"):
            logger = logging.getLogger(name)
            for h in [h for h in logger.handlers if getattr(h, "baseFilename", "").startswith(str(tmp_path))]:
                h.close()
                logger.removeHandler(h)


def test_production_logs_to_logs_folder_by_default():
    assert ProductionConfig.LOG_DIR.endswith("logs")
    assert ProductionConfig.SQLALCHEMY_ENGINE_OPTIONS["pool_size"] >= 8  # one connection per waitress thread


# ---- backups -----------------------------------------------------------------

@pytest.fixture
def pg_tools(app):
    if not (ops_service.find_pg_tool("pg_dump") and ops_service.find_pg_tool("pg_restore")):
        pytest.skip("pg_dump/pg_restore not found (set PG_BIN in .env to test backups)")


@pytest.fixture
def backup_dir(app, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "BACKUP_DIR", str(tmp_path))
    return tmp_path


def test_backup_written_verified_and_named(app, pg_tools, backup_dir):
    path, size, removed = ops_service.backup_database()
    assert os.path.isfile(path) and size > 1000 and removed == []
    assert os.path.basename(path).startswith("attendance_test_") and path.endswith(".dump")
    assert not [f for f in os.listdir(backup_dir) if f.endswith(".part")]


def test_old_backups_are_pruned(app, pg_tools, backup_dir, monkeypatch):
    monkeypatch.setitem(app.config, "BACKUP_KEEP", 2)
    for stamp in ("20260101_000000", "20260102_000000", "20260103_000000"):
        (backup_dir / f"attendance_test_{stamp}.dump").write_bytes(b"old")
    (backup_dir / "notes.txt").write_text("not a backup")
    _, _, removed = ops_service.backup_database()
    assert sorted(removed) == ["attendance_test_20260101_000000.dump", "attendance_test_20260102_000000.dump"]
    assert sorted(os.listdir(backup_dir))[-1] == "notes.txt"  # other files untouched


def test_failed_backup_leaves_nothing_behind(app, pg_tools, backup_dir, monkeypatch):
    monkeypatch.setitem(app.config, "SQLALCHEMY_DATABASE_URI",
                        "postgresql+psycopg://attendance_app:wrong-password@localhost:5432/attendance_test")
    with pytest.raises(BackupError):
        ops_service.backup_database()
    assert os.listdir(backup_dir) == []


def test_missing_tools_explained(app, backup_dir, monkeypatch):
    monkeypatch.setattr(ops_service, "find_pg_tool", lambda name: None)
    with pytest.raises(BackupError) as exc:
        ops_service.backup_database()
    assert "PG_BIN" in str(exc.value)


def test_password_is_not_on_the_command_line(app, backup_dir, monkeypatch):
    seen = {}

    def fake_run(command, env, timeout):
        seen.setdefault("commands", []).append(command)
        seen["env"] = env
        if command[0].endswith("pg_dump"):
            open(command[command.index("--file") + 1], "wb").write(b"x")
        return type("R", (), {"stdout": "TABLE DATA public users"})()

    monkeypatch.setattr(ops_service, "find_pg_tool", lambda name: "/bin/" + name)
    monkeypatch.setattr(ops_service, "_run", fake_run)
    monkeypatch.setitem(app.config, "SQLALCHEMY_DATABASE_URI", "postgresql+psycopg://u:S3cret!@localhost:5432/attendance_test")
    ops_service.backup_database()
    assert all("S3cret!" not in " ".join(c) for c in seen["commands"])
    assert seen["env"]["PGPASSWORD"] == "S3cret!"


def test_restore_brings_back_the_backed_up_data(app, pg_tools, backup_dir):
    db.session.add(Section(code="BEFORE", name="Exists at backup time"))
    db.session.commit()
    path, _, _ = ops_service.backup_database()
    db.session.add(Section(code="AFTER", name="Added after the backup"))
    db.session.commit()

    ops_service.restore_database(path)
    codes = {s.code for s in Section.query.all()}
    assert codes == {"BEFORE"}
    # The append-only audit trigger came back with the restore too.
    assert db.session.execute(text("SELECT 1 FROM pg_trigger WHERE tgname = 'trg_attendance_audit_append_only'")).scalar() == 1


def test_restore_command_requires_typing_the_database_name(cli, app, backup_dir, tmp_path):
    fake = tmp_path / "x.dump"
    fake.write_bytes(b"x")
    result = cli.invoke(args=["restore-db", str(fake)], input="wrong_name\n")
    assert result.exit_code != 0 and "Nothing was changed" in result.output


def test_restore_of_missing_file(cli, app):
    result = cli.invoke(args=["restore-db", "/no/such/file.dump", "--yes"])
    assert result.exit_code != 0 and "Restore failed" in result.output


def test_backup_command_reports_success(cli, app, pg_tools, backup_dir):
    result = cli.invoke(args=["backup-db"])
    assert result.exit_code == 0 and "Backup written and verified" in result.output


# ---- the waitress server -------------------------------------------------------

def test_serve_refuses_to_start_when_a_check_fails(monkeypatch, capsys):
    import serve as serve_module

    monkeypatch.setattr(ops_service, "CHECKS", [("Bad", lambda: (FAIL, "broken"))])
    monkeypatch.setattr(serve_module, "serve", lambda *a, **k: pytest.fail("must not serve"))
    monkeypatch.setattr("sys.argv", ["serve.py"])
    monkeypatch.setenv("FLASK_CONFIG", "testing")
    with pytest.raises(SystemExit) as exc:
        serve_module.main()
    assert exc.value.code == 1 and "Not starting" in capsys.readouterr().out


def test_serve_starts_waitress_with_safe_settings(monkeypatch):
    import serve as serve_module

    captured = {}
    monkeypatch.setattr(ops_service, "CHECKS", [("Good", lambda: (OK, "fine"))])
    monkeypatch.setattr(serve_module, "serve", lambda app, **kw: captured.update(kw))
    monkeypatch.setattr("sys.argv", ["serve.py"])
    monkeypatch.setenv("FLASK_CONFIG", "testing")
    monkeypatch.delenv("HOST", raising=False)
    serve_module.main()
    assert captured["host"] == "127.0.0.1" and captured["port"] == 8000 and captured["threads"] == 8
    assert captured["ident"] == "" and captured["max_request_body_size"] == 6 * 1024 * 1024
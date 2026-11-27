"""Operations: readiness checks and PostgreSQL backups.

Readiness (`flask --app wsgi check`, also run by serve.py before starting):
every item is OK, WARN (works, but should be fixed) or FAIL (do not start).

Backups (`flask --app wsgi backup-db` / `restore-db`) use PostgreSQL's own pg_dump and
pg_restore. The password is passed through the PGPASSWORD environment variable, never on
the command line (where other programs could see it).
"""
import glob
import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime

from flask import current_app
from sqlalchemy import text
from sqlalchemy.engine import make_url

from ..extensions import db
from ..utils.timeutils import college_tz

OK, WARN, FAIL = "OK", "WARN", "FAIL"
HTML5_QRCODE_SHA256 = "660b12437b1d747e3e68b8be0685c08cb728140110ad213f167b14b66f8b1d8e"
VENDOR_FILES = ["bootstrap/bootstrap.min.css", "bootstrap/bootstrap.bundle.min.js", "html5-qrcode/html5-qrcode.min.js"]


@dataclass
class Check:
    name: str
    status: str
    message: str


# ---- readiness -------------------------------------------------------------

def _check(name, function):
    try:
        status, message = function()
    except Exception as exc:  # a check must never crash the report
        status, message = FAIL, f"{type(exc).__name__}: {exc}"
    return Check(name, status, message)


def _config():
    config = current_app.config
    name = config.get("CONFIG_NAME")
    if name != "production":
        return WARN, f"running with '{name}' settings; set FLASK_CONFIG=production in .env for real use"
    if config.get("DEBUG"):
        return FAIL, "DEBUG is on in production"
    return OK, "production settings"


def _database():
    db.session.execute(text("SELECT 1"))
    return OK, f"connected to '{db.engine.url.database}'"


def _migrations():
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(current_app.extensions["migrate"].migrate.get_config()).get_current_head()
    current = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if current != head:
        return FAIL, f"database at '{current}', code expects '{head}': run flask --app wsgi db upgrade"
    return OK, f"database schema up to date ({head})"


def _face_models():
    from .face_engine import missing_or_damaged_models

    missing = missing_or_damaged_models(current_app.config["FACE_MODEL_DIR"])
    if missing:
        return FAIL, f"missing or damaged: {', '.join(missing)}; run flask --app wsgi download-face-models"
    return OK, "face models present and verified"


def _vendor_files():
    static = os.path.join(current_app.root_path, "static", "vendor")
    missing = [name for name in VENDOR_FILES if not os.path.isfile(os.path.join(static, name))]
    if missing:
        return FAIL, f"missing: {', '.join(missing)} (README setup step 6)"
    with open(os.path.join(static, "html5-qrcode", "html5-qrcode.min.js"), "rb") as handle:
        if hashlib.sha256(handle.read()).hexdigest() != HTML5_QRCODE_SHA256:
            return WARN, "html5-qrcode.min.js is not the official 2.3.8 file; download it again"
    return OK, "Bootstrap and QR scanner files present"


def _host_settings():
    config = current_app.config
    if config.get("CONFIG_NAME") != "production":
        return OK, "not checked outside production"
    problems = []
    if not config.get("TRUSTED_HOSTS"):
        problems.append("TRUSTED_HOSTS not set")
    if config.get("TRUSTED_PROXY_COUNT", 0) < 1:
        problems.append("TRUSTED_PROXY_COUNT is 0 (set 1 when behind a reverse proxy or tunnel)")
    return (WARN, "; ".join(problems)) if problems else (OK, "host and proxy settings set")


def _timezone():
    now = datetime.now(college_tz())
    return OK, f"college time {now.strftime('%d %b %Y %H:%M')} ({current_app.config['APP_TIMEZONE']})"


def _pg_tools():
    if find_pg_tool("pg_dump") and find_pg_tool("pg_restore"):
        return OK, "pg_dump and pg_restore found (backups possible)"
    return WARN, "pg_dump not found; set PG_BIN in .env to PostgreSQL's bin folder to enable backups"


CHECKS = [
    ("Settings", _config),
    ("Database", _database),
    ("Database schema", _migrations),
    ("Face models", _face_models),
    ("Browser libraries", _vendor_files),
    ("Host and proxy", _host_settings),
    ("Time zone", _timezone),
    ("Backup tools", _pg_tools),
]


def readiness():
    return [_check(name, function) for name, function in CHECKS]


# ---- backups ---------------------------------------------------------------

def find_pg_tool(name):
    """Path to pg_dump / pg_restore: PG_BIN from .env, then PATH, then the usual Windows folders."""
    exe = name + (".exe" if os.name == "nt" else "")
    pg_bin = current_app.config.get("PG_BIN")
    if pg_bin and os.path.isfile(os.path.join(pg_bin, exe)):
        return os.path.join(pg_bin, exe)
    found = shutil.which(name)
    if found:
        return found
    for candidate in sorted(glob.glob(rf"C:\Program Files\PostgreSQL\*\bin\{exe}"), reverse=True):
        return candidate  # newest version first
    return None


def _connection_args(uri):
    url = make_url(uri)
    args = ["--host", url.host or "localhost", "--port", str(url.port or 5432), "--username", url.username or ""]
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    return args, env, url.database


class BackupError(RuntimeError):
    pass


def _run(command, env, timeout):
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise BackupError(f"{os.path.basename(command[0])} took longer than {timeout} s") from exc
    if result.returncode != 0:
        raise BackupError(result.stderr.strip()[-500:] or f"{os.path.basename(command[0])} failed")
    return result


def backup_database(now=None):
    """Write a compressed backup to BACKUP_DIR, verify it, and keep only the newest BACKUP_KEEP.

    Returns (path, size_in_bytes, removed_old_files).
    """
    config = current_app.config
    pg_dump, pg_restore = find_pg_tool("pg_dump"), find_pg_tool("pg_restore")
    if not pg_dump or not pg_restore:
        raise BackupError("pg_dump / pg_restore not found. Set PG_BIN in .env to PostgreSQL's bin folder.")
    args, env, database = _connection_args(config["SQLALCHEMY_DATABASE_URI"])
    folder = config["BACKUP_DIR"]
    os.makedirs(folder, exist_ok=True)

    stamp = (now or datetime.now(college_tz())).strftime("%Y%m%d_%H%M%S")
    final = os.path.join(folder, f"{database}_{stamp}.dump")
    partial = final + ".part"
    try:
        _run([pg_dump, *args, "--format=custom", "--no-owner", "--file", partial, database], env, timeout=900)
        # Verify: pg_restore must be able to read the table of contents.
        listing = _run([pg_restore, "--list", partial], env, timeout=120)
        if "TABLE DATA" not in listing.stdout:
            raise BackupError("the backup file does not contain table data")
    except Exception:
        if os.path.exists(partial):
            os.remove(partial)
        raise
    os.replace(partial, final)  # only a verified backup gets its final name

    backups = sorted(glob.glob(os.path.join(folder, f"{database}_*.dump")), reverse=True)
    removed = []
    for old in backups[config["BACKUP_KEEP"]:]:
        os.remove(old)
        removed.append(os.path.basename(old))
    current_app.logger.info("database backup written: %s", final)
    return final, os.path.getsize(final), removed


def restore_database(path):
    """Replace the current database contents with a backup file. Destroys current data."""
    config = current_app.config
    pg_restore = find_pg_tool("pg_restore")
    if not pg_restore:
        raise BackupError("pg_restore not found. Set PG_BIN in .env to PostgreSQL's bin folder.")
    if not os.path.isfile(path):
        raise BackupError(f"no such backup file: {path}")
    args, env, database = _connection_args(config["SQLALCHEMY_DATABASE_URI"])
    db.session.remove()
    db.engine.dispose()  # drop the app's own connections so tables can be replaced
    _run([pg_restore, *args, "--dbname", database, "--clean", "--if-exists", "--no-owner",
          "--single-transaction", path], env, timeout=1800)
    current_app.logger.warning("database restored from %s", path)
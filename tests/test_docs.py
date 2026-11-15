"""Module 7, part 3: the documents must match the real app.

A checklist that names a button or command that does not exist sends faculty hunting
mid-class, so these tests compare the documents with the code.
"""
import glob
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = ["README.md", "DEPLOYMENT.md"] + sorted(os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "docs", "*.md")))
BUILT_IN_FLASK_COMMANDS = {"db", "run", "shell", "routes"}

# Quoted phrases in the guides that are examples or explanations, not text on screen.
NOT_ON_SCREEN = {"face check failed, seen in class"}


def _read(name):
    return open(os.path.join(ROOT, name), encoding="utf-8").read()


def _quoted(text):
    """Quoted phrases, with line wrapping inside a phrase joined back into single spaces."""
    return {" ".join(phrase.split()) for phrase in re.findall(r'"([^"]+)"', _without_code(text))}


def _readme_headings():
    return {line.lstrip("#").strip() for line in _read("README.md").splitlines() if line.startswith("## ")}


def _without_code(text):
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.sub(r"`[^`]*`", "", text)


def _ui_text():
    """All text a user can see: templates, scripts, and messages in the Python code."""
    parts = []
    for pattern in ("app/templates/**/*.html", "app/static/js/*.js", "app/**/*.py"):
        for path in glob.glob(os.path.join(ROOT, pattern), recursive=True):
            parts.append(open(path, encoding="utf-8").read())
    text = "\n".join(parts)
    # Template variables shown with their default values, as a user would see them.
    return text.replace("{{ config.ATTENDANCE_WARNING_PERCENT }}", "75")


def test_documents_exist():
    for name in DOCS:
        assert len(_read(name)) > 500, name
    assert {"docs/faculty-checklist.md", "docs/student-guide.md", "docs/admin-checklist.md"} <= set(DOCS)


@pytest.mark.parametrize("name", DOCS)
def test_every_flask_command_in_the_docs_exists(web_app, name):
    available = set(web_app.cli.list_commands(None) if hasattr(web_app.cli, "list_commands") else web_app.cli.commands)
    available |= set(web_app.cli.commands) | BUILT_IN_FLASK_COMMANDS
    used = set(re.findall(r"flask --app wsgi ([a-z][a-z-]*)", _read(name)))
    assert used - available == set(), f"{name} mentions commands that do not exist"


def test_admin_checklist_names_real_postman_requests():
    collection = json.loads(_read("postman/qr-attendance-admin.postman_collection.json"))
    names = {folder["name"] for folder in collection["item"]}
    names |= {item["name"] for folder in collection["item"] for item in folder["item"]}
    quoted = _quoted(_read("docs/admin-checklist.md"))
    unknown = quoted - names - _readme_headings()   # README sections may be quoted too
    assert unknown == set(), f"the admin checklist names Postman requests that do not exist: {unknown}"


@pytest.mark.parametrize("name", ["docs/faculty-checklist.md", "docs/student-guide.md"])
def test_guides_quote_real_buttons_and_messages(name):
    ui = _ui_text().lower()
    quoted = _quoted(_read(name)) - NOT_ON_SCREEN - _readme_headings()
    missing = sorted(phrase for phrase in quoted if phrase.lower() not in ui)
    assert missing == [], f"{name} quotes text that is not in the app: {missing}"


def test_production_env_example_only_uses_real_settings():
    code = _read("app/config.py") + _read("app/__init__.py") + _read("serve.py")
    keys = re.findall(r"^([A-Z_]+)=", _read(".env.production.example"), flags=re.M)
    assert keys and [k for k in keys if k not in code] == []


def test_deployment_guide_points_to_files_that_exist():
    text = _read("DEPLOYMENT.md")
    for path in ("scripts\\start_server.bat", "scripts\\backup_db.bat", ".env.production.example",
                 "docs/admin-checklist.md", "docs/faculty-checklist.md", "docs/student-guide.md"):
        assert path in text, path
        assert os.path.exists(os.path.join(ROOT, path.replace("\\", os.sep))), path
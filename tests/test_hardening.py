"""Module 7, part 1: security hardening checks.

Several tests scan the whole code base, so a later change cannot quietly undo the
hardening (for example by adding an inline script that the security policy would block).
"""
import logging
import os
import re

import pytest

from app import CONTENT_SECURITY_POLICY, create_app
from app.config import ProductionConfig
from app.extensions import csrf, db
from app.models import Role
from tests.factories import TEST_PASSWORD, make_user

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "app", "templates")
STATIC = os.path.join(ROOT, "app", "static")


def _templates():
    for folder, _, files in os.walk(TEMPLATES):
        for name in files:
            if name.endswith(".html"):
                path = os.path.join(folder, name)
                yield os.path.relpath(path, TEMPLATES), open(path, encoding="utf-8").read()


# ---- Content-Security-Policy -------------------------------------------------

@pytest.mark.parametrize("path", ["/login", "/api/admin/users", "/health", "/does-not-exist"])
def test_security_headers_on_every_kind_of_response(client, path):
    headers = client.get(path).headers
    assert headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert headers["X-Frame-Options"] == "DENY" and headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert headers["Cross-Origin-Resource-Policy"] == "same-origin"


def test_policy_is_strict():
    directives = dict(d.split(" ", 1) for d in CONTENT_SECURITY_POLICY.split("; "))
    assert directives["script-src"] == "'self'"           # no inline or remote scripts
    assert "unsafe" not in CONTENT_SECURITY_POLICY         # no 'unsafe-inline' / 'unsafe-eval'
    assert directives["frame-ancestors"] == "'none'" and directives["object-src"] == "'none'"


def test_templates_have_no_inline_code_the_policy_would_block():
    problems = []
    for name, html in _templates():
        if re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html):
            problems.append(f"{name}: inline <script>")
        if re.search(r"\son[a-z]+\s*=", html):
            problems.append(f"{name}: inline event handler (onclick= etc.)")
        if re.search(r'\sstyle\s*=\s*"', html):
            problems.append(f"{name}: style= attribute")
        if re.search(r"\|\s*safe\b", html):
            problems.append(f"{name}: |safe turns off HTML escaping")
    assert problems == []


def test_every_local_script_and_stylesheet_exists():
    # Catches a missing file (like a JS file not copied over) before a student does.
    missing = []
    for name, html in _templates():
        for filename in re.findall(r"url_for\('static', filename='([^']+)'\)", html):
            if filename.startswith("vendor/"):
                continue  # downloaded separately (README setup step 6)
            if not os.path.isfile(os.path.join(STATIC, filename)):
                missing.append(f"{name}: {filename}")
    assert missing == []


def test_javascript_has_no_eval_or_unsafe_html_except_the_qr_svg():
    findings = []
    for name in os.listdir(os.path.join(STATIC, "js")):
        code = open(os.path.join(STATIC, "js", name), encoding="utf-8").read()
        if re.search(r"\beval\s*\(|new Function\s*\(", code):
            findings.append(f"{name}: eval")
        for line in code.splitlines():
            if "innerHTML" in line and "qr.svg" not in line:
                findings.append(f"{name}: innerHTML ({line.strip()[:60]})")
    assert findings == []


# ---- CSRF -----------------------------------------------------------------------

def test_only_the_token_api_skips_csrf(web_app):
    assert csrf._exempt_views == set()
    assert {getattr(bp, "name", bp) for bp in csrf._exempt_blueprints} == {"admin_api"}


# ---- production settings --------------------------------------------------------

def test_production_settings_are_safe():
    assert ProductionConfig.DEBUG is False and getattr(ProductionConfig, "TESTING", False) is False
    assert ProductionConfig.SESSION_COOKIE_SECURE and ProductionConfig.REMEMBER_COOKIE_SECURE
    assert ProductionConfig.SESSION_COOKIE_NAME == "__Host-session"
    assert ProductionConfig.REMEMBER_COOKIE_NAME == "__Host-remember"
    assert ProductionConfig.HSTS_ENABLED is True
    assert ProductionConfig.SESSION_COOKIE_HTTPONLY and ProductionConfig.SESSION_COOKIE_SAMESITE == "Lax"


def test_hsts_only_when_enabled_and_on_https(web_app, client):
    assert "Strict-Transport-Security" not in client.get("/login", base_url="https://x.example").headers
    web_app.config["HSTS_ENABLED"] = True
    try:
        assert "Strict-Transport-Security" not in client.get("/login").headers  # plain http
        assert client.get("/login", base_url="https://x.example").headers["Strict-Transport-Security"] == "max-age=31536000"
    finally:
        web_app.config["HSTS_ENABLED"] = False


def test_trusted_hosts_rejects_other_host_names(web_app, client):
    web_app.config["TRUSTED_HOSTS"] = ["attendance.college.edu"]
    try:
        assert client.get("/health", base_url="https://attendance.college.edu").status_code == 200
        rejected = client.get("/health", base_url="https://evil.example")
        assert rejected.status_code == 400 and b"unknown host" in rejected.data
    finally:
        web_app.config["TRUSTED_HOSTS"] = None


def test_trusted_hosts_from_environment(monkeypatch):
    import importlib

    import app.config as config_module

    monkeypatch.setenv("TRUSTED_HOSTS", " attendance.college.edu , www.attendance.college.edu ")
    reloaded = importlib.reload(config_module)
    try:
        assert reloaded.BaseConfig.TRUSTED_HOSTS == ["attendance.college.edu", "www.attendance.college.edu"]
    finally:
        monkeypatch.delenv("TRUSTED_HOSTS")
        importlib.reload(config_module)


def test_production_warns_without_trusted_hosts(monkeypatch, caplog):
    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", "x" * 40)
    monkeypatch.setattr(ProductionConfig, "SQLALCHEMY_DATABASE_URI", "postgresql+psycopg://u:p@localhost:5432/attendance_prod")
    monkeypatch.setattr(ProductionConfig, "TRUSTED_HOSTS", None)
    monkeypatch.setattr(ProductionConfig, "LOG_DIR", "")  # tests must not write log files into the project
    with caplog.at_level(logging.WARNING):
        create_app("production")
    assert "TRUSTED_HOSTS is not set" in caplog.text


# ---- security logging -----------------------------------------------------------

def test_failed_login_logged_without_password(client, caplog):
    make_user(role=Role.FACULTY, username="fac001")
    db.session.commit()
    with caplog.at_level(logging.WARNING):
        client.post("/login", data={"username": "FAC001", "password": "Secret-Guess-99"}, environ_base={"REMOTE_ADDR": "10.9.8.7"})
    assert "failed login for 'fac001' from 10.9.8.7" in caplog.text
    assert "Secret-Guess-99" not in caplog.text


def test_successful_login_not_logged_as_failure(client, caplog):
    make_user(role=Role.FACULTY, username="fac001", must_change_password=False)
    db.session.commit()
    with caplog.at_level(logging.WARNING):
        client.post("/login", data={"username": "fac001", "password": TEST_PASSWORD})
    assert "failed login" not in caplog.text


def test_rejected_admin_token_is_logged(client, caplog):
    with caplog.at_level(logging.WARNING):
        client.get("/api/admin/users", headers={"Authorization": "Bearer wrong"}, environ_base={"REMOTE_ADDR": "10.1.1.1"})
    assert "rejected admin API call to /api/admin/users from 10.1.1.1" in caplog.text
"""Module 0, part 1: app factory, configuration safety and health check."""
import pytest
from sqlalchemy.exc import OperationalError

from app import create_app
from app.config import ProductionConfig, TestingConfig
from app.extensions import db, login_manager


def test_app_uses_testing_config(app):
    assert app.config["TESTING"] is True
    assert app.config["CONFIG_NAME"] == "testing"


def test_health_reports_database_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "database": "ok"}


def test_health_returns_503_when_database_fails(client, monkeypatch):
    def broken_execute(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(db.session, "execute", broken_execute)
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.get_json()["database"] == "unreachable"
    # Internal error text must never reach the client.
    assert "connection refused" not in resp.get_data(as_text=True)


def test_unknown_route_is_404(client):
    assert client.get("/does-not-exist").status_code == 404


def test_unknown_config_name_raises():
    with pytest.raises(ValueError, match="Unknown config"):
        create_app("staging")


def test_missing_secret_key_raises(monkeypatch):
    monkeypatch.setattr(TestingConfig, "SECRET_KEY", None)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app("testing")


def test_missing_database_url_raises(monkeypatch):
    monkeypatch.setattr(TestingConfig, "SQLALCHEMY_DATABASE_URI", None)
    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL"):
        create_app("testing")


def test_refuses_to_test_against_non_test_database(monkeypatch):
    monkeypatch.setattr(
        TestingConfig,
        "SQLALCHEMY_DATABASE_URI",
        "postgresql+psycopg://u:p@localhost:5432/attendance_dev",
    )
    with pytest.raises(RuntimeError, match="_test"):
        create_app("testing")


def test_rejects_non_postgres_database(monkeypatch):
    monkeypatch.setattr(TestingConfig, "SQLALCHEMY_DATABASE_URI", "sqlite:///attendance_test")
    with pytest.raises(RuntimeError, match="postgresql"):
        create_app("testing")


def test_production_rejects_short_secret(monkeypatch):
    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", "too-short")
    monkeypatch.setattr(
        ProductionConfig,
        "SQLALCHEMY_DATABASE_URI",
        "postgresql+psycopg://u:p@localhost:5432/attendance_prod",
    )
    with pytest.raises(RuntimeError, match="at least 32"):
        create_app("production")


def test_production_cookies_are_secure():
    assert ProductionConfig.SESSION_COOKIE_SECURE is True
    assert ProductionConfig.REMEMBER_COOKIE_SECURE is True


def test_cookies_hidden_from_javascript(app):
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["REMEMBER_COOKIE_HTTPONLY"] is True


def test_session_protection_tolerates_network_changes():
    # "strong" would log students out when their phone switches Wi-Fi <-> data.
    assert login_manager.session_protection == "basic"


def test_negative_proxy_count_raises(monkeypatch):
    monkeypatch.setattr(TestingConfig, "TRUSTED_PROXY_COUNT", -1)
    with pytest.raises(RuntimeError, match="TRUSTED_PROXY_COUNT"):
        create_app("testing")
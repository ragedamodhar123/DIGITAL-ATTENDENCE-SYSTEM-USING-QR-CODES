"""Module 1, part 2: safe redirects and rate-limit keys."""
import pytest

from app.utils.http import is_safe_next
from app.utils.ratelimit import current_user_key, login_ip_key, login_user_key


@pytest.mark.parametrize("target", ["/", "/student/scan", "/faculty/course/3?tab=live", "/a/b%2Fc"])
def test_safe_next_accepts_same_site_paths(target):
    assert is_safe_next(target) is True


@pytest.mark.parametrize(
    "target",
    [
        None,
        "",
        "https://evil.com",
        "//evil.com",
        "/\\evil.com",
        "\\\\evil.com",
        "javascript:alert(1)",
        "evil.com/path",
        "/ok\r\nSet-Cookie: x=1",
        "/" + "a" * 600,
        123,
    ],
)
def test_safe_next_rejects_open_redirects(target):
    assert is_safe_next(target) is False


def test_login_key_combines_ip_and_normalized_username(app):
    with app.test_request_context(
        "/login", method="POST", data={"username": "  CS2024-017 "}, environ_base={"REMOTE_ADDR": "10.0.0.5"}
    ):
        assert login_user_key() == "login:10.0.0.5:cs2024-017"
        assert login_ip_key() == "login-ip:10.0.0.5"


def test_login_key_reads_json_body(app):
    with app.test_request_context(
        "/api/login", method="POST", json={"username": "Fac001"}, environ_base={"REMOTE_ADDR": "10.0.0.5"}
    ):
        assert login_user_key() == "login:10.0.0.5:fac001"


@pytest.mark.parametrize("body", [{"username": 5}, {"username": None}, ["not", "a", "dict"]])
def test_login_key_survives_odd_json(app, body):
    with app.test_request_context("/api/login", method="POST", json=body, environ_base={"REMOTE_ADDR": "1.2.3.4"}):
        assert login_user_key() == "login:1.2.3.4:"


def test_login_key_truncates_huge_username(app):
    with app.test_request_context("/login", method="POST", data={"username": "x" * 5000}):
        assert len(login_user_key()) < 100


def test_two_students_on_same_college_ip_get_different_keys(app):
    keys = set()
    for name in ["stu001", "stu002"]:
        with app.test_request_context(
            "/login", method="POST", data={"username": name}, environ_base={"REMOTE_ADDR": "10.0.0.1"}
        ):
            keys.add(login_user_key())
    assert len(keys) == 2


def test_anonymous_user_key_uses_ip(app):
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "10.9.9.9"}):
        assert current_user_key() == "anon:10.9.9.9"
"""Module 1, part 1: login, password rules, password change, admin token."""
import pytest
from argon2 import PasswordHasher

from app.extensions import db
from app.models import Role, load_user
from app.services import auth_service
from app.services.auth_service import (
    LOGIN_FAILED_MESSAGE,
    admin_from_auth_header,
    authenticate,
    change_password,
    password_problems,
)
from app.services.errors import AuthenticationFailed, ValidationFailed
from app.utils.security import generate_api_token, verify_password
from tests.factories import TEST_PASSWORD, make_user

NEW_PASSWORD = "Blue-River-42"


# ---- authenticate ---------------------------------------------------------

def test_login_succeeds_and_records_time():
    user = make_user(role=Role.STUDENT, username="cs2024-017")
    assert user.last_login_at is None
    assert authenticate("cs2024-017", TEST_PASSWORD) == user
    assert user.last_login_at is not None


def test_login_username_ignores_case_and_spaces():
    user = make_user(username="fac001")
    assert authenticate("  FAC001 ", TEST_PASSWORD) == user


def test_login_password_is_case_sensitive():
    make_user(username="fac001")
    with pytest.raises(AuthenticationFailed):
        authenticate("fac001", TEST_PASSWORD.upper())


def _login_error(username, password):
    with pytest.raises(AuthenticationFailed) as exc:
        authenticate(username, password)
    return exc.value


def test_all_login_failures_look_identical():
    make_user(username="fac001")
    make_user(username="gone01", is_active=False)
    wrong_password = _login_error("fac001", "Wrong-pass-1")
    unknown_user = _login_error("nobody99", TEST_PASSWORD)
    disabled_user = _login_error("gone01", TEST_PASSWORD)
    for err in (wrong_password, unknown_user, disabled_user):
        assert err.message == LOGIN_FAILED_MESSAGE
        assert err.status_code == 401
        assert err.details == {}


def test_admin_cannot_log_in_with_password_even_if_correct():
    make_user(role=Role.ADMIN, username="admin1")  # factory gives a known password
    assert _login_error("admin1", TEST_PASSWORD).message == LOGIN_FAILED_MESSAGE


def test_unknown_username_still_runs_a_hash_check(monkeypatch):
    calls = []
    real_verify = auth_service.verify_password
    monkeypatch.setattr(
        auth_service, "verify_password", lambda h, p: calls.append(1) or real_verify(h, p)
    )
    _login_error("nobody99", TEST_PASSWORD)
    assert len(calls) == 1  # same work as a real user, so timing reveals nothing


@pytest.mark.parametrize(
    "username,password",
    [
        (None, TEST_PASSWORD),
        ("fac001", None),
        ("", TEST_PASSWORD),
        ("   ", TEST_PASSWORD),
        ("fac001", ""),
        (123, TEST_PASSWORD),
        ("x" * 65, TEST_PASSWORD),
        ("fac001", "a" * 129),
        (["fac001"], TEST_PASSWORD),
    ],
)
def test_bad_login_input_fails_cleanly(username, password):
    make_user(username="fac001")
    assert _login_error(username, password).message == LOGIN_FAILED_MESSAGE


def test_outdated_hash_is_upgraded_on_login():
    weak_hash = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(TEST_PASSWORD)
    user = make_user(username="fac001", password_hash=weak_hash)
    authenticate("fac001", TEST_PASSWORD)
    assert user.password_hash != weak_hash
    assert verify_password(user.password_hash, TEST_PASSWORD)


def test_must_change_password_does_not_block_login_itself():
    # The redirect to the change-password page happens in the routes (part 2).
    user = make_user(role=Role.STUDENT, username="stu001")
    assert user.must_change_password is True
    assert authenticate("stu001", TEST_PASSWORD) == user


# ---- password rules ------------------------------------------------------

def test_good_password_has_no_problems():
    assert password_problems(NEW_PASSWORD, username="stu001") == []


@pytest.mark.parametrize(
    "password,expected_fragment",
    [
        ("", "Enter a new password"),
        (None, "Enter a new password"),
        ("Ab1", "at least 8"),
        ("a1" * 70, "at most 128"),
        ("12345678901", "one letter"),
        ("OnlyLetters", "one number"),
        (" Spaced-pass1", "start or end with a space"),
        ("Stu001-rocks", "username"),
        ("Password123", "too common"),
        ("DEMO@12345", "too common"),
    ],
)
def test_password_rules(password, expected_fragment):
    problems = password_problems(password, username="stu001")
    assert any(expected_fragment in p for p in problems), problems


def test_unicode_password_allowed():
    assert password_problems("ശരി-Password-7", username="stu001") == []


# ---- change_password -----------------------------------------------------

def test_change_password_success_completes_first_login():
    user = make_user(role=Role.STUDENT, username="stu001")
    old_cookie = user.get_id()

    change_password(user, TEST_PASSWORD, NEW_PASSWORD, NEW_PASSWORD)

    assert user.must_change_password is False
    assert user.password_changed_at is not None
    assert user.session_version == 2
    assert verify_password(user.password_hash, NEW_PASSWORD)
    assert not verify_password(user.password_hash, TEST_PASSWORD)
    # Every old login (including other phones) is now signed out.
    assert load_user(old_cookie) is None
    assert load_user(user.get_id()) == user


def test_change_password_then_login_with_new_password():
    user = make_user(role=Role.STUDENT, username="stu001")
    change_password(user, TEST_PASSWORD, NEW_PASSWORD, NEW_PASSWORD)
    assert authenticate("stu001", NEW_PASSWORD) == user
    with pytest.raises(AuthenticationFailed):
        authenticate("stu001", TEST_PASSWORD)


def _change_error(user, current, new, confirm):
    before = (user.password_hash, user.session_version, user.must_change_password)
    with pytest.raises(ValidationFailed) as exc:
        change_password(user, current, new, confirm)
    db.session.refresh(user)
    assert (user.password_hash, user.session_version, user.must_change_password) == before
    return exc.value.details


def test_wrong_current_password_changes_nothing():
    user = make_user(role=Role.STUDENT)
    details = _change_error(user, "Not-it-123", NEW_PASSWORD, NEW_PASSWORD)
    assert "current_password" in details


def test_mismatched_confirmation_changes_nothing():
    user = make_user(role=Role.STUDENT)
    details = _change_error(user, TEST_PASSWORD, NEW_PASSWORD, NEW_PASSWORD + "x")
    assert details == {"confirm_password": ["Passwords do not match."]}


def test_weak_new_password_changes_nothing():
    user = make_user(role=Role.STUDENT)
    details = _change_error(user, TEST_PASSWORD, "short1", "short1")
    assert "new_password" in details


def test_new_password_must_differ_from_current():
    user = make_user(role=Role.STUDENT)
    details = _change_error(user, TEST_PASSWORD, TEST_PASSWORD, TEST_PASSWORD)
    assert any("different" in p for p in details["new_password"])


def test_all_problems_reported_together():
    user = make_user(role=Role.STUDENT)
    details = _change_error(user, "wrong", "abc", "xyz")
    assert set(details) == {"current_password", "new_password", "confirm_password"}


@pytest.mark.parametrize("bad", [None, 123])
def test_non_string_inputs_handled(bad):
    user = make_user(role=Role.STUDENT)
    details = _change_error(user, bad, bad, bad)
    assert "current_password" in details and "new_password" in details


# ---- admin API token -----------------------------------------------------

def _admin_with_token(**extra):
    token, token_hash = generate_api_token()
    admin = make_user(role=Role.ADMIN, api_token_hash=token_hash, **extra)
    return admin, token


def test_valid_bearer_token_returns_admin():
    admin, token = _admin_with_token()
    assert admin_from_auth_header(f"Bearer {token}") == admin
    assert admin_from_auth_header(f"bearer {token}") == admin
    assert admin_from_auth_header(f"  Bearer   {token}  ") == admin


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Bearer ", "Token abc", "Basic dXNlcjpwYXNz", "Bearer a b", "Bearer " + "x" * 300],
)
def test_malformed_headers_rejected(header):
    _admin_with_token()
    assert admin_from_auth_header(header) is None


def test_wrong_token_rejected():
    _admin_with_token()
    assert admin_from_auth_header("Bearer not-the-real-token") is None


def test_deactivated_admin_token_rejected():
    admin, token = _admin_with_token(is_active=False)
    assert admin_from_auth_header(f"Bearer {token}") is None


def test_rotated_token_old_one_stops_working():
    admin, old_token = _admin_with_token()
    new_token, new_hash = generate_api_token()
    admin.api_token_hash = new_hash
    db.session.flush()
    assert admin_from_auth_header(f"Bearer {old_token}") is None
    assert admin_from_auth_header(f"Bearer {new_token}") == admin
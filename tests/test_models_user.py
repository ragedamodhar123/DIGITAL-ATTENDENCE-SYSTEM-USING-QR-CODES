"""Module 0, part 2: User model rules, enforced by the database itself."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Role, User, load_user
from tests.factories import make_section, make_user


def _expect_db_rejects(constraint_fragment):
    """Flush and assert PostgreSQL rejects it, naming the expected constraint."""
    with pytest.raises(IntegrityError) as exc:
        db.session.flush()
    db.session.rollback()
    assert constraint_fragment in str(exc.value)


def test_username_and_email_are_normalized():
    user = make_user(username="  CS2024-017 ", email="  Ravi@College.EDU ")
    assert user.username == "cs2024-017"
    assert user.email == "ravi@college.edu"


def test_blank_email_becomes_null():
    assert make_user(email="   ").email is None


def test_many_users_without_email_allowed():
    make_user()
    make_user()  # two NULL emails must not clash


def test_username_unique_ignoring_case():
    make_user(username="fac001")
    db.session.add(User(username="FAC001", full_name="Dup", role=Role.FACULTY, password_hash="x"))
    _expect_db_rejects("uq_users_username")


def test_email_unique_ignoring_case():
    make_user(email="a@x.edu")
    db.session.add(
        User(username="other1", full_name="Dup", role=Role.FACULTY, password_hash="x", email="A@X.edu")
    )
    _expect_db_rejects("uq_users_email")


def test_raw_sql_cannot_bypass_lowercase_rule():
    # Even an INSERT from pgAdmin is checked, not just inserts through the app.
    with pytest.raises(IntegrityError) as exc:
        db.session.execute(
            text(
                "INSERT INTO users (username, full_name, role, password_hash) "
                "VALUES ('UPPER01', 'X', 'faculty', 'x')"
            )
        )
    db.session.rollback()
    assert "username_lowercase" in str(exc.value)


def test_invalid_role_rejected():
    db.session.add(User(username="bad01", full_name="X", role="superuser", password_hash="x"))
    _expect_db_rejects("valid_role")


def test_short_username_rejected():
    db.session.add(User(username="ab", full_name="X", role=Role.FACULTY, password_hash="x"))
    _expect_db_rejects("username_min_length")


def test_blank_full_name_rejected():
    db.session.add(User(username="blank01", full_name="   ", role=Role.FACULTY, password_hash="x"))
    _expect_db_rejects("full_name_not_blank")


def test_student_must_have_section():
    db.session.add(User(username="stu01", full_name="X", role=Role.STUDENT, password_hash="x"))
    _expect_db_rejects("student_has_section")


def test_faculty_cannot_have_section():
    section = make_section()
    db.session.add(
        User(username="fac01", full_name="X", role=Role.FACULTY, password_hash="x", section=section)
    )
    _expect_db_rejects("student_has_section")


def test_api_token_only_for_admin():
    make_user(role=Role.ADMIN, api_token_hash="a" * 64)  # allowed
    db.session.add(
        User(username="fac02", full_name="X", role=Role.FACULTY, password_hash="x", api_token_hash="b" * 64)
    )
    _expect_db_rejects("api_token_admin_only")


def test_new_user_defaults():
    user = make_user()
    db.session.refresh(user)
    assert user.must_change_password is True
    assert user.is_active is True
    assert user.session_version == 1
    assert user.created_at is not None and user.created_at.tzinfo is not None


def test_role_helpers():
    assert make_user(role=Role.ADMIN).is_admin
    assert make_user(role=Role.FACULTY).is_faculty
    assert make_user(role=Role.STUDENT).is_student


def test_cannot_delete_section_that_has_students():
    student = make_user(role=Role.STUDENT)
    db.session.delete(student.section)
    _expect_db_rejects("foreign key")


# ---- Flask-Login loader -------------------------------------------------

def test_loader_returns_user_for_current_cookie():
    user = make_user()
    assert load_user(user.get_id()) == user


def test_loader_rejects_old_session_version():
    user = make_user()
    old_cookie = user.get_id()
    user.session_version += 1  # e.g. after a password change
    db.session.flush()
    assert load_user(old_cookie) is None
    assert load_user(user.get_id()) == user


def test_loader_rejects_deactivated_user():
    user = make_user()
    cookie = user.get_id()
    user.is_active = False
    db.session.flush()
    assert load_user(cookie) is None


@pytest.mark.parametrize("raw", [None, "", "abc", "5", "5:", ":1", "x:y", "99999:1"])
def test_loader_handles_malformed_or_unknown_ids(raw):
    assert load_user(raw) is None

"""Module 2, part 1: input validation for user management."""
import pytest

from app.schemas.common import load_or_fail
from app.schemas.user_schemas import (
    MAX_BULK_STUDENTS,
    FacultyCreateSchema,
    StudentBulkCreateSchema,
    StudentCreateSchema,
    UserListQuerySchema,
    UserOutSchema,
    UserUpdateSchema,
)
from app.services.errors import ValidationFailed
from tests.factories import make_user

VALID_STUDENT = {"username": "CS2024-017", "full_name": "  Asha Nair ", "section_code": " cse-a ", "email": " Asha@College.EDU "}


def _errors(schema, data):
    with pytest.raises(ValidationFailed) as exc:
        load_or_fail(schema, data)
    return exc.value.details


def test_student_input_is_cleaned():
    data = load_or_fail(StudentCreateSchema(), VALID_STUDENT)
    assert data == {
        "username": "cs2024-017",
        "full_name": "Asha Nair",
        "section_code": "CSE-A",
        "email": "asha@college.edu",
    }


def test_missing_required_fields_reported_together():
    details = _errors(StudentCreateSchema(), {})
    assert set(details) == {"username", "full_name", "section_code"}


def test_unknown_field_rejected():
    # A typo must not be silently ignored.
    details = _errors(FacultyCreateSchema(), {"username": "fac001", "fullname": "X"})
    assert "fullname" in details and "full_name" in details


@pytest.mark.parametrize("username", ["ab", "has space", "bad!", "-dash-first", "x" * 65, "", 12345, None])
def test_bad_usernames(username):
    assert "username" in _errors(FacultyCreateSchema(), {"username": username, "full_name": "X"})


@pytest.mark.parametrize("email", ["not-an-email", "a@", "@b.com", 5, "a" * 250 + "@x.edu"])
def test_bad_email(email):
    assert "email" in _errors(FacultyCreateSchema(), {"username": "fac001", "full_name": "X", "email": email})


@pytest.mark.parametrize("email", [None, "", "   "])
def test_blank_email_becomes_none(email):
    data = load_or_fail(FacultyCreateSchema(), {"username": "fac001", "full_name": "X", "email": email})
    assert data["email"] is None


@pytest.mark.parametrize("name", ["", "   ", "x" * 121, 42])
def test_bad_full_name(name):
    assert "full_name" in _errors(FacultyCreateSchema(), {"username": "fac001", "full_name": name})


def test_no_body_reported_clearly():
    assert "_body" in _errors(FacultyCreateSchema(), None)


def test_body_that_is_not_an_object():
    assert _errors(FacultyCreateSchema(), ["not", "an", "object"])


def test_bulk_errors_point_at_the_row():
    details = _errors(StudentBulkCreateSchema(), {"students": [VALID_STUDENT, {"username": "x"}]})
    assert "1" in str(details["students"])  # row 1 is the bad one


@pytest.mark.parametrize("students", [[], [VALID_STUDENT] * (MAX_BULK_STUDENTS + 1)])
def test_bulk_size_limits(students):
    assert "students" in _errors(StudentBulkCreateSchema(), {"students": students})


def test_update_accepts_partial_and_rejects_username_change():
    assert load_or_fail(UserUpdateSchema(), {"full_name": "New Name"}) == {"full_name": "New Name"}
    assert "username" in _errors(UserUpdateSchema(), {"username": "new-roll"})
    assert "role" in _errors(UserUpdateSchema(), {"role": "admin"})


def test_list_query_defaults_and_limits():
    assert load_or_fail(UserListQuerySchema(), {}) == {"page": 1, "per_page": 25}
    assert "per_page" in _errors(UserListQuerySchema(), {"per_page": "500"})
    assert "role" in _errors(UserListQuerySchema(), {"role": "admin"})
    # Extra query parameters (cache busters etc.) are ignored, not errors.
    assert load_or_fail(UserListQuerySchema(), {"_": "123", "page": "2"})["page"] == 2


def test_output_never_includes_secrets():
    user = make_user(role="student", api_token_hash=None)
    dumped = UserOutSchema().dump(user)
    assert "password_hash" not in dumped and "api_token_hash" not in dumped
    assert dumped["section_code"] == user.section.code
    assert dumped["face_enrolled"] is False


def test_output_face_enrolled_is_none_for_faculty():
    assert UserOutSchema().dump(make_user(role="faculty"))["face_enrolled"] is None
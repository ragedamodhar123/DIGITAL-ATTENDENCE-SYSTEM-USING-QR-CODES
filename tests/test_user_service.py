"""Module 2, part 1: user management rules."""
import re
import struct

import pytest

from app.extensions import db
from app.models import Enrollment, FaceEmbedding, Role, User, load_user
from app.services import user_service as svc
from app.services.auth_service import authenticate
from app.services.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.utils.security import generate_temporary_password, verify_password
from tests.factories import assign_faculty, enroll, make_course, make_section, make_user

TEMP_PATTERN = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}$")


@pytest.fixture
def admin():
    return make_user(role=Role.ADMIN, username="admin1")


@pytest.fixture
def section():
    return make_section(code="CSE-A")


def _student_data(username="cs2024-001", section_code="CSE-A", **extra):
    data = {"username": username, "full_name": "Test Student", "email": None, "section_code": section_code}
    data.update(extra)
    return data


# ---- temporary passwords ---------------------------------------------------

def test_temporary_password_is_readable_and_random():
    samples = {generate_temporary_password() for _ in range(200)}
    assert len(samples) == 200
    for pw in samples:
        assert TEMP_PATTERN.match(pw)
        assert not set(pw) & set("01lio5s2zb8")  # no look-alike characters
        assert any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw)


# ---- create ----------------------------------------------------------------

def test_admin_creates_faculty_who_can_log_in_with_temp_password(admin):
    user, temp = svc.create_faculty(admin, {"username": "fac001", "full_name": "Dr Rao", "email": "rao@college.edu"})
    assert user.role == Role.FACULTY and user.must_change_password is True
    assert verify_password(user.password_hash, temp)
    assert temp not in user.password_hash
    assert authenticate("fac001", temp) == user


def test_admin_creates_student_in_section(admin, section):
    user, _ = svc.create_student(admin, _student_data())
    assert user.section == section and user.is_student


def test_non_admin_cannot_create_users(section):
    faculty = make_user(role=Role.FACULTY)
    with pytest.raises(PermissionDenied):
        svc.create_student(faculty, _student_data())
    with pytest.raises(PermissionDenied):
        svc.create_faculty(None, {"username": "fac009", "full_name": "X"})


def test_deactivated_admin_cannot_act(section):
    admin = make_user(role=Role.ADMIN, is_active=False)
    with pytest.raises(PermissionDenied):
        svc.create_student(admin, _student_data())


def test_unknown_or_retired_section_rejected(admin):
    with pytest.raises(ValidationFailed) as exc:
        svc.create_student(admin, _student_data(section_code="NOPE"))
    assert "section_code" in exc.value.details
    make_section(code="OLD", is_active=False)
    with pytest.raises(ValidationFailed):
        svc.create_student(admin, _student_data(section_code="OLD"))


def test_duplicate_username_and_email_reported(admin, section):
    make_user(username="cs2024-001", email="a@x.edu", role=Role.STUDENT, section=section)
    with pytest.raises(Conflict) as exc:
        svc.create_student(admin, _student_data(email="a@x.edu"))
    assert set(exc.value.details) == {"username", "email"}


def test_username_cannot_clash_with_admin_or_faculty(admin, section):
    with pytest.raises(Conflict):
        svc.create_student(admin, _student_data(username="admin1"))


# ---- bulk create -----------------------------------------------------------

def test_bulk_create_all_succeed(admin, section):
    make_section(code="ECE-B")
    rows = [_student_data(f"cs2024-{i:03d}") for i in range(1, 4)] + [_student_data("ec2024-001", "ECE-B")]
    created = svc.bulk_create_students(admin, rows)
    assert [u.username for u, _ in created] == [r["username"] for r in rows]
    assert len({temp for _, temp in created}) == 4  # every student gets their own password


def test_bulk_create_is_all_or_nothing_with_every_problem_listed(admin, section):
    make_user(username="cs2024-002", role=Role.STUDENT, section=section)
    rows = [
        _student_data("cs2024-001", email="dup@x.edu"),
        _student_data("cs2024-002"),                      # already exists
        _student_data("cs2024-003", section_code="NOPE"),  # unknown section
        _student_data("cs2024-001"),                      # duplicate inside request
        _student_data("cs2024-004", email="dup@x.edu"),   # duplicate email inside request
    ]
    before = User.query.count()
    with pytest.raises(ValidationFailed) as exc:
        svc.bulk_create_students(admin, rows)
    problems = exc.value.details["students"]
    assert set(problems) == {"1", "2", "3", "4"}
    assert "row 0" in problems["3"]["username"][0]
    assert User.query.count() == before  # nothing saved


# ---- read ------------------------------------------------------------------

def test_list_filters_search_and_pagination(admin, section):
    other = make_section(code="ECE-B")
    for i in range(5):
        make_user(role=Role.STUDENT, username=f"cs2024-{i:03d}", section=section)
    make_user(role=Role.STUDENT, username="ec2024-001", section=other, full_name="Meera Pillai")
    make_user(role=Role.FACULTY, username="fac001", is_active=False)

    users, total = svc.list_users(admin, {"role": "student", "page": 1, "per_page": 2})
    assert total == 6 and len(users) == 2
    assert svc.list_users(admin, {"section_code": "ECE-B"})[1] == 1
    assert svc.list_users(admin, {"active": False})[0][0].username == "fac001"
    assert svc.list_users(admin, {"q": "meera"})[1] == 1
    assert svc.list_users(admin, {"q": "%"})[1] == 0  # wildcard characters are literal
    assert all(u.role != Role.ADMIN for u in svc.list_users(admin, {})[0])


def test_admin_accounts_are_invisible_to_the_api(admin):
    with pytest.raises(NotFound):
        svc.get_user(admin, admin.id)


@pytest.mark.parametrize("bad_id", [0, -1, 999999, "1", None, True])
def test_bad_ids_are_not_found(admin, bad_id):
    make_user(role=Role.FACULTY)
    with pytest.raises(NotFound):
        svc.get_user(admin, bad_id)


# ---- update ----------------------------------------------------------------

def test_update_name_email_and_section(admin, section):
    student = make_user(role=Role.STUDENT, section=section, email="old@x.edu")
    new_section = make_section(code="CSE-B")
    svc.update_user(admin, student.id, {"full_name": "New Name", "email": None, "section_code": "CSE-B"})
    assert (student.full_name, student.email, student.section) == ("New Name", None, new_section)


def test_update_rejects_section_for_faculty_and_taken_email(admin, section):
    faculty = make_user(role=Role.FACULTY)
    with pytest.raises(ValidationFailed):
        svc.update_user(admin, faculty.id, {"section_code": "CSE-A"})
    make_user(email="taken@x.edu")
    with pytest.raises(Conflict):
        svc.update_user(admin, faculty.id, {"email": "taken@x.edu"})


def test_update_keeping_own_email_is_fine(admin):
    faculty = make_user(role=Role.FACULTY, email="me@x.edu")
    svc.update_user(admin, faculty.id, {"email": "me@x.edu", "full_name": "Same Email"})


def test_empty_update_rejected(admin):
    with pytest.raises(ValidationFailed):
        svc.update_user(admin, make_user().id, {})


# ---- activate / deactivate -------------------------------------------------

def test_deactivate_signs_out_and_blocks_login(admin):
    faculty = make_user(role=Role.FACULTY, username="fac001")
    cookie = faculty.get_id()
    svc.set_active(admin, faculty.id, False)
    assert load_user(cookie) is None
    with pytest.raises(Exception):
        authenticate("fac001", "Correct-Horse-9")


def test_deactivate_twice_is_harmless_and_reactivate_works(admin):
    faculty = make_user(role=Role.FACULTY)
    svc.set_active(admin, faculty.id, False)
    version = faculty.session_version
    svc.set_active(admin, faculty.id, False)
    assert faculty.session_version == version
    svc.set_active(admin, faculty.id, True)
    assert faculty.is_active is True


# ---- issuing temporary passwords --------------------------------------------

def _teaching_setup():
    faculty = make_user(role=Role.FACULTY, username="fac001")
    student = make_user(role=Role.STUDENT, username="stu001", must_change_password=False)
    course = make_course()
    assign_faculty(course, faculty)
    enroll(course, student)
    return faculty, student, course


def test_admin_issues_password_and_old_sessions_end(admin):
    student = make_user(role=Role.STUDENT, must_change_password=False)
    cookie = student.get_id()
    user, temp = svc.issue_temporary_password(admin, student.id)
    assert user.must_change_password is True
    assert verify_password(user.password_hash, temp)
    assert load_user(cookie) is None


def test_faculty_issues_password_for_own_student():
    faculty, student, _ = _teaching_setup()
    _, temp = svc.issue_temporary_password(faculty, student.id)
    assert authenticate("stu001", temp) == student


def test_faculty_cannot_issue_for_student_not_in_their_course():
    faculty, _, _ = _teaching_setup()
    stranger = make_user(role=Role.STUDENT)
    with pytest.raises(PermissionDenied) as exc:
        svc.issue_temporary_password(faculty, stranger.id)
    assert "courses you teach" in exc.value.message


def test_faculty_loses_access_when_enrolment_dropped_or_course_retired():
    faculty, student, course = _teaching_setup()
    Enrollment.query.filter_by(student_id=student.id).one().is_active = False
    with pytest.raises(PermissionDenied):
        svc.issue_temporary_password(faculty, student.id)
    Enrollment.query.filter_by(student_id=student.id).one().is_active = True
    course.is_active = False
    with pytest.raises(PermissionDenied):
        svc.issue_temporary_password(faculty, student.id)


def test_faculty_cannot_reset_other_faculty_or_students_cannot_reset_anyone():
    faculty, student, _ = _teaching_setup()
    colleague = make_user(role=Role.FACULTY)
    with pytest.raises(PermissionDenied):
        svc.issue_temporary_password(faculty, colleague.id)
    with pytest.raises(PermissionDenied):
        svc.issue_temporary_password(student, faculty.id)


def test_cannot_issue_password_for_deactivated_account(admin):
    student = make_user(role=Role.STUDENT, is_active=False)
    with pytest.raises(ValidationFailed):
        svc.issue_temporary_password(admin, student.id)


def test_issuing_password_keeps_enrolled_face(admin):
    # A forgotten password is not a new face: enrolment stays.
    student = make_user(role=Role.STUDENT)
    db.session.add(FaceEmbedding(student=student, model_name="t", dim=128, embedding=struct.pack("<128f", *[0.0] * 128)))
    db.session.flush()
    svc.issue_temporary_password(admin, student.id)
    assert student.face is not None
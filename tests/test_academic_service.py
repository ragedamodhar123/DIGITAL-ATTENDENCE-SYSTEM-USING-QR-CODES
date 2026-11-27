"""Module 2, part 2: sections, courses, faculty assignment, enrolment."""
import pytest

from app.extensions import db
from app.models import Enrollment, Role
from app.schemas.academic_schemas import (
    CourseCreateSchema,
    CourseOutSchema,
    EnrolStudentsSchema,
    RosterEntryOutSchema,
    SectionCreateSchema,
)
from app.schemas.common import load_or_fail
from app.services import academic_service as svc
from app.services.course_service import courses_for_faculty, courses_for_student
from app.services.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.services.user_service import can_issue_credentials
from tests.factories import assign_faculty, enroll, make_course, make_section, make_user


@pytest.fixture
def admin():
    return make_user(role=Role.ADMIN, username="admin1")


@pytest.fixture
def course():
    return make_course(code="DS301", term="2026-ODD")


def _students(section, count, prefix="stu"):
    return [make_user(role=Role.STUDENT, username=f"{prefix}{i:03d}", section=section) for i in range(count)]


# ---- schemas ---------------------------------------------------------------

def test_codes_are_cleaned_and_checked():
    data = load_or_fail(CourseCreateSchema(), {"code": " ds301 ", "name": "Data Structures", "term": "2026-odd"})
    assert (data["code"], data["term"]) == ("DS301", "2026-ODD")
    for bad in ["DS 301", "-DS301", "DS301!", "x" * 33]:
        with pytest.raises(ValidationFailed) as exc:
            load_or_fail(CourseCreateSchema(), {"code": bad, "name": "X", "term": "2026-ODD"})
        assert "code" in exc.value.details


def test_section_needs_code_and_name():
    with pytest.raises(ValidationFailed) as exc:
        load_or_fail(SectionCreateSchema(), {})
    assert set(exc.value.details) == {"code", "name"}


def test_enrol_list_limits():
    with pytest.raises(ValidationFailed):
        load_or_fail(EnrolStudentsSchema(), {"usernames": []})
    with pytest.raises(ValidationFailed):
        load_or_fail(EnrolStudentsSchema(), {"usernames": ["ok-name", "bad name"]})


# ---- sections --------------------------------------------------------------

def test_create_and_list_sections_with_student_counts(admin):
    section = svc.create_section(admin, {"code": "CSE-A", "name": "CSE A"})
    _students(section, 3)
    make_user(role=Role.STUDENT, section=section, is_active=False)  # not counted
    svc.create_section(admin, {"code": "ECE-B", "name": "ECE B"})
    listed = {s.code: s.student_count for s in svc.list_sections(admin, {})}
    assert listed == {"CSE-A": 3, "ECE-B": 0}


def test_duplicate_section_code_rejected(admin):
    svc.create_section(admin, {"code": "CSE-A", "name": "CSE A"})
    with pytest.raises(Conflict) as exc:
        svc.create_section(admin, {"code": "CSE-A", "name": "Again"})
    assert "code" in exc.value.details


def test_rename_section_and_code_clash(admin):
    a = svc.create_section(admin, {"code": "CSE-A", "name": "CSE A"})
    svc.create_section(admin, {"code": "CSE-B", "name": "CSE B"})
    svc.update_section(admin, a.id, {"code": "CSE-A1", "name": "CSE A (2024)"})
    assert (a.code, a.name) == ("CSE-A1", "CSE A (2024)")
    with pytest.raises(Conflict):
        svc.update_section(admin, a.id, {"code": "CSE-B"})
    svc.update_section(admin, a.id, {"code": "CSE-A1"})  # same code: fine


def test_retire_and_restore_section(admin):
    section = svc.create_section(admin, {"code": "CSE-A", "name": "CSE A"})
    svc.set_section_active(admin, section.id, False)
    assert svc.list_sections(admin, {"active": False})[0].code == "CSE-A"
    svc.set_section_active(admin, section.id, True)
    assert section.is_active is True


def test_section_search_treats_wildcards_literally(admin):
    svc.create_section(admin, {"code": "CSE-A", "name": "CSE A"})
    assert svc.list_sections(admin, {"q": "%"}) == []
    assert len(svc.list_sections(admin, {"q": "cse"})) == 1


# ---- courses ---------------------------------------------------------------

def test_create_course_and_same_code_in_another_term(admin):
    svc.create_course(admin, {"code": "DS301", "name": "Data Structures", "term": "2026-ODD"})
    svc.create_course(admin, {"code": "DS301", "name": "Data Structures", "term": "2027-ODD"})
    with pytest.raises(Conflict):
        svc.create_course(admin, {"code": "DS301", "name": "Dup", "term": "2026-ODD"})


def test_update_course_into_existing_code_term_rejected(admin):
    svc.create_course(admin, {"code": "DS301", "name": "DS", "term": "2026-ODD"})
    other = svc.create_course(admin, {"code": "DB302", "name": "DB", "term": "2026-ODD"})
    with pytest.raises(Conflict):
        svc.update_course(admin, other.id, {"code": "DS301"})
    svc.update_course(admin, other.id, {"term": "2026-EVEN", "name": "Databases"})
    assert (other.term, other.name) == ("2026-EVEN", "Databases")


def test_list_courses_filters_and_output(admin):
    fac = make_user(role=Role.FACULTY, username="fac001")
    ds = make_course(code="DS301", term="2026-ODD")
    old = make_course(code="DS301", term="2025-ODD")
    old.is_active = False
    assign_faculty(ds, fac)
    enroll(ds, make_user(role=Role.STUDENT))

    courses, total = svc.list_courses(admin, {"term": "2026-ODD"})
    assert total == 1
    dumped = CourseOutSchema().dump(courses[0])
    assert dumped["faculty"] == ["fac001"] and dumped["student_count"] == 1
    assert svc.list_courses(admin, {"active": False})[1] == 1
    assert svc.list_courses(admin, {"faculty": "fac001"})[1] == 1
    assert svc.list_courses(admin, {"q": "%"})[1] == 0


@pytest.mark.parametrize("bad_id", [0, -5, 99999, None, True, "1"])
def test_unknown_course_ids(admin, bad_id):
    with pytest.raises(NotFound):
        svc.get_course(admin, bad_id)


def test_retired_course_is_read_only_and_hidden(admin, course):
    fac = make_user(role=Role.FACULTY, username="fac001")
    student = make_user(role=Role.STUDENT, username="stu001")
    assign_faculty(course, fac)
    enroll(course, student)
    svc.set_course_active(admin, course.id, False)

    with pytest.raises(ValidationFailed):
        svc.enrol_students(admin, course.id, ["stu001"])
    with pytest.raises(ValidationFailed):
        svc.assign_faculty(admin, course.id, "fac001")
    assert courses_for_student(student.id) == []
    assert courses_for_faculty(fac.id) == []

    svc.set_course_active(admin, course.id, True)
    assert courses_for_student(student.id) == [course]


# ---- faculty assignment ----------------------------------------------------

def test_assign_and_unassign_faculty(admin, course):
    fac = make_user(role=Role.FACULTY, username="fac001")
    _, created = svc.assign_faculty(admin, course.id, "fac001")
    assert created is True
    _, created_again = svc.assign_faculty(admin, course.id, "fac001")
    assert created_again is False  # repeat is harmless
    assert courses_for_faculty(fac.id)[0][0] == course

    svc.unassign_faculty(admin, course.id, "fac001")
    assert courses_for_faculty(fac.id) == []
    with pytest.raises(NotFound):
        svc.unassign_faculty(admin, course.id, "fac001")


@pytest.mark.parametrize("setup", ["missing", "student", "inactive"])
def test_assign_rejects_non_faculty(admin, course, setup):
    if setup == "student":
        make_user(role=Role.STUDENT, username="who001")
    if setup == "inactive":
        make_user(role=Role.FACULTY, username="who001", is_active=False)
    with pytest.raises(ValidationFailed) as exc:
        svc.assign_faculty(admin, course.id, "who001")
    assert "username" in exc.value.details


def test_unassigned_faculty_loses_password_reset_power(admin, course):
    fac = make_user(role=Role.FACULTY, username="fac001")
    student = make_user(role=Role.STUDENT)
    svc.assign_faculty(admin, course.id, "fac001")
    svc.enrol_students(admin, course.id, [student.username])
    assert can_issue_credentials(fac, student)
    svc.unassign_faculty(admin, course.id, "fac001")
    assert not can_issue_credentials(fac, student)


# ---- enrolment -------------------------------------------------------------

def test_enrol_students_across_sections(admin, course):
    a, b = make_section(code="CSE-A"), make_section(code="ECE-B")
    make_user(role=Role.STUDENT, username="cs001", section=a)
    make_user(role=Role.STUDENT, username="ec001", section=b)
    summary = svc.enrol_students(admin, course.id, ["cs001", "ec001", "cs001"])  # repeat ignored
    assert summary == {"enrolled": ["cs001", "ec001"], "re_enrolled": [], "already_enrolled": []}


def test_enrol_is_all_or_nothing_and_lists_every_problem(admin, course):
    make_user(role=Role.STUDENT, username="cs001")
    make_user(role=Role.FACULTY, username="fac001")
    make_user(role=Role.STUDENT, username="gone01", is_active=False)
    with pytest.raises(ValidationFailed) as exc:
        svc.enrol_students(admin, course.id, ["cs001", "nobody", "fac001", "gone01"])
    assert set(exc.value.details["usernames"]) == {"nobody", "fac001", "gone01"}
    assert Enrollment.query.count() == 0  # cs001 was NOT enrolled either


def test_enrol_reports_already_enrolled_and_re_enrols_dropped(admin, course):
    s1, s2 = make_user(role=Role.STUDENT, username="cs001"), make_user(role=Role.STUDENT, username="cs002")
    enroll(course, s1)
    db.session.add(Enrollment(course=course, student=s2, is_active=False))
    db.session.flush()
    summary = svc.enrol_students(admin, course.id, ["cs001", "cs002"])
    assert summary == {"enrolled": [], "re_enrolled": ["cs002"], "already_enrolled": ["cs001"]}
    assert Enrollment.query.filter_by(student_id=s2.id).count() == 1  # same row reused


def test_enrol_whole_section_skips_deactivated(admin, course):
    section = make_section(code="CSE-A")
    _students(section, 4)
    make_user(role=Role.STUDENT, username="zz-gone", section=section, is_active=False)
    summary = svc.enrol_section(admin, course.id, "CSE-A")
    assert len(summary["enrolled"]) == 4 and "zz-gone" not in summary["enrolled"]
    again = svc.enrol_section(admin, course.id, "CSE-A")
    assert len(again["already_enrolled"]) == 4 and again["enrolled"] == []


@pytest.mark.parametrize("case", ["unknown", "retired", "empty"])
def test_enrol_section_problems(admin, course, case):
    if case == "retired":
        make_section(code="OLD", is_active=False)
    if case == "empty":
        make_section(code="OLD")
    with pytest.raises(ValidationFailed) as exc:
        svc.enrol_section(admin, course.id, "OLD")
    assert "section_code" in exc.value.details


def test_drop_keeps_history_and_can_be_undone(admin, course):
    student = make_user(role=Role.STUDENT, username="cs001")
    svc.enrol_students(admin, course.id, ["cs001"])
    svc.drop_student(admin, course.id, "cs001")
    assert courses_for_student(student.id) == []
    assert Enrollment.query.filter_by(student_id=student.id).one().is_active is False
    svc.drop_student(admin, course.id, "cs001")  # dropping twice is harmless
    with pytest.raises(NotFound):
        svc.drop_student(admin, course.id, "never-enrolled")
    assert svc.enrol_students(admin, course.id, ["cs001"])["re_enrolled"] == ["cs001"]


# ---- roster ----------------------------------------------------------------

def test_roster_for_admin_and_teaching_faculty_only(admin, course):
    fac, other_fac = make_user(role=Role.FACULTY, username="fac001"), make_user(role=Role.FACULTY)
    assign_faculty(course, fac)
    s1 = make_user(role=Role.STUDENT, username="cs001")
    s2 = make_user(role=Role.STUDENT, username="cs002")
    enroll(course, s1)
    db.session.add(Enrollment(course=course, student=s2, is_active=False))
    db.session.flush()

    _, roster = svc.course_roster(admin, course.id)
    assert [e.student.username for e in roster] == ["cs001"]
    _, with_dropped = svc.course_roster(fac, course.id, include_dropped=True)
    assert [RosterEntryOutSchema().dump(e)["is_active"] for e in with_dropped] == [True, False]

    with pytest.raises(PermissionDenied):
        svc.course_roster(other_fac, course.id)
    with pytest.raises(PermissionDenied):
        svc.course_roster(s1, course.id)


# ---- permissions -----------------------------------------------------------

def test_only_admin_changes_academic_data(course):
    fac = make_user(role=Role.FACULTY)
    assign_faculty(course, fac)
    for call in (
        lambda: svc.create_section(fac, {"code": "X", "name": "X"}),
        lambda: svc.create_course(fac, {"code": "X", "name": "X", "term": "T"}),
        lambda: svc.enrol_students(fac, course.id, ["a"]),
        lambda: svc.assign_faculty(fac, course.id, "a"),
        lambda: svc.list_courses(fac, {}),
    ):
        with pytest.raises(PermissionDenied):
            call()
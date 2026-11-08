"""Module 0, part 2: sections, courses, faculty assignments and enrolments."""
import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Course, CourseFaculty, Enrollment, Role, Section
from tests.factories import assign_faculty, enroll, make_course, make_section, make_user


def _expect_db_rejects(fragment=None):
    with pytest.raises(IntegrityError) as exc:
        db.session.flush()
    db.session.rollback()
    if fragment:
        assert fragment in str(exc.value)


# ---- Section / Course ---------------------------------------------------

def test_codes_are_normalized_to_uppercase():
    assert make_section(code=" cse-a-2024 ").code == "CSE-A-2024"
    course = make_course(code=" cs301 ", term=" 2026-odd ")
    assert (course.code, course.term) == ("CS301", "2026-ODD")


def test_section_code_unique():
    make_section(code="CSE-A")
    db.session.add(Section(code="cse-a", name="Dup"))
    _expect_db_rejects("uq_sections_code")


def test_same_course_code_allowed_in_different_terms():
    make_course(code="CS301", term="2026-ODD")
    make_course(code="CS301", term="2027-ODD")


def test_same_course_code_and_term_rejected():
    make_course(code="CS301", term="2026-ODD")
    db.session.add(Course(code="cs301", term="2026-odd", name="Dup"))
    _expect_db_rejects("uq_courses_code_term")


@pytest.mark.parametrize("field", ["code", "term"])
def test_blank_course_code_or_term_rejected(field):
    values = {"code": "CS999", "term": "2026-ODD", "name": "X"}
    values[field] = "   "
    db.session.add(Course(**values))
    _expect_db_rejects("not_blank")


# ---- Faculty assignment -------------------------------------------------

def test_faculty_can_teach_many_courses_and_course_can_have_many_faculty():
    fac_a, fac_b = make_user(), make_user()
    c1, c2 = make_course(), make_course()
    assign_faculty(c1, fac_a)
    assign_faculty(c2, fac_a)
    assign_faculty(c1, fac_b)
    assert {link.course for link in fac_a.teaching_links} == {c1, c2}
    assert {link.faculty for link in c1.faculty_links} == {fac_a, fac_b}


def test_same_faculty_cannot_be_assigned_twice():
    fac, course = make_user(), make_course()
    assign_faculty(course, fac)
    db.session.add(CourseFaculty(course_id=course.id, faculty_id=fac.id))
    _expect_db_rejects()


@pytest.mark.parametrize("role", [Role.STUDENT, Role.ADMIN])
def test_non_faculty_cannot_be_assigned_to_teach(role):
    course, not_faculty = make_course(), make_user(role=role)
    db.session.add(CourseFaculty(course=course, faculty=not_faculty))
    _expect_db_rejects()


def test_cannot_turn_assigned_faculty_into_student_silently():
    section = make_section()
    fac, course = make_user(), make_course()
    assign_faculty(course, fac)
    fac.role = Role.STUDENT
    fac.section = section
    _expect_db_rejects()


# ---- Enrolment ----------------------------------------------------------

def test_course_can_have_students_from_many_sections():
    course = make_course()
    s1 = make_user(role=Role.STUDENT, section=make_section(code="CSE-A"))
    s2 = make_user(role=Role.STUDENT, section=make_section(code="ECE-B"))
    enroll(course, s1)
    enroll(course, s2)
    assert {e.student.section.code for e in course.enrollments} == {"CSE-A", "ECE-B"}


def test_student_can_take_many_courses():
    student = make_user(role=Role.STUDENT)
    enroll(make_course(), student)
    enroll(make_course(), student)
    assert len(student.enrollments) == 2


def test_duplicate_enrolment_rejected():
    course, student = make_course(), make_user(role=Role.STUDENT)
    enroll(course, student)
    db.session.add(Enrollment(course_id=course.id, student_id=student.id))
    _expect_db_rejects("uq_enrollments_course_id_student_id")


@pytest.mark.parametrize("role", [Role.FACULTY, Role.ADMIN])
def test_non_student_cannot_be_enrolled(role):
    course, user = make_course(), make_user(role=role)
    db.session.add(Enrollment(course=course, student=user))
    _expect_db_rejects()


def test_new_enrolment_is_active():
    enrollment = enroll(make_course(), make_user(role=Role.STUDENT))
    db.session.refresh(enrollment)
    assert enrollment.is_active is True


# ---- Deletion protection ------------------------------------------------

def test_cannot_delete_course_with_enrolments():
    course = make_course()
    enroll(course, make_user(role=Role.STUDENT))
    db.session.delete(course)
    _expect_db_rejects("foreign key")


def test_cannot_delete_student_with_enrolments():
    student = make_user(role=Role.STUDENT)
    enroll(make_course(), student)
    db.session.delete(student)
    _expect_db_rejects("foreign key")


def test_cannot_delete_faculty_with_assignments():
    fac = make_user()
    assign_faculty(make_course(), fac)
    db.session.delete(fac)
    _expect_db_rejects("foreign key")

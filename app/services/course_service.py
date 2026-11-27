"""Read-only course lookups used by the dashboards. Grows in Modules 2 and 5."""
from sqlalchemy import and_, func

from ..extensions import db
from ..models import Course, CourseFaculty, Enrollment


def courses_for_student(student_id):
    """Active courses the student is actively enrolled in, newest term first."""
    return (
        Course.query.join(Enrollment, Enrollment.course_id == Course.id)
        .filter(
            Enrollment.student_id == student_id,
            Enrollment.is_active.is_(True),
            Course.is_active.is_(True),
        )
        .order_by(Course.term.desc(), Course.code)
        .all()
    )


def courses_for_faculty(faculty_id):
    """Active courses the faculty member teaches, with active-enrolment counts.

    Returns a list of (course, student_count) pairs.
    """
    rows = (
        db.session.query(Course, func.count(Enrollment.id))
        .join(CourseFaculty, CourseFaculty.course_id == Course.id)
        .outerjoin(Enrollment, and_(Enrollment.course_id == Course.id, Enrollment.is_active.is_(True)))
        .filter(CourseFaculty.faculty_id == faculty_id, Course.is_active.is_(True))
        .group_by(Course.id)
        .order_by(Course.term.desc(), Course.code)
        .all()
    )
    return [(course, count) for course, count in rows]
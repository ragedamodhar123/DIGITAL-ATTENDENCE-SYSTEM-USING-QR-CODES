"""A student's own attendance: per-course totals and class-by-class history.

What counts towards the percentage: finished classes (closed sessions) that
have a record for this student. Every enrolled student gets a record when a
class closes, so:
- a class still running does not count yet (it would unfairly lower the number);
- a student enrolled part-way through the term is only counted from then on.
Percentage = present / (present + absent), rounded to one decimal.

Students only ever see their own records. The reason a faculty member gave for a
change is not shown to students (it may mention other people); they see that the
record was "marked by faculty".
"""
from dataclasses import dataclass

from flask import current_app
from sqlalchemy import func

from ..extensions import db
from ..models import AttendanceRecord, AttendanceSession, AttendanceStatus, Course, Enrollment, SessionStatus
from .common import valid_id
from .errors import NotFound
from .session_service import close_expired_sessions


@dataclass
class CourseSummary:
    course: Course
    present: int
    absent: int
    enrolled: bool                    # still actively enrolled (False = dropped, history only)
    open_session: AttendanceSession = None

    @property
    def total(self):
        return self.present + self.absent

    @property
    def percent(self):
        return round(100.0 * self.present / self.total, 1) if self.total else None

    @property
    def below_threshold(self):
        threshold = current_app.config["ATTENDANCE_WARNING_PERCENT"]
        return self.percent is not None and self.percent < threshold


def _tallies(student_id):
    """{course_id: {"present": n, "absent": m}} over closed sessions."""
    rows = (
        db.session.query(AttendanceSession.course_id, AttendanceRecord.status, func.count(AttendanceRecord.id))
        .join(AttendanceSession, AttendanceSession.id == AttendanceRecord.session_id)
        .filter(AttendanceRecord.student_id == student_id, AttendanceSession.status == SessionStatus.CLOSED)
        .group_by(AttendanceSession.course_id, AttendanceRecord.status)
    )
    tallies = {}
    for course_id, status, count in rows:
        tallies.setdefault(course_id, {})[status] = count
    return tallies


def course_summaries(student, now=None):
    """Every course the student is enrolled in, or has attendance history in."""
    close_expired_sessions(now)
    tallies = _tallies(student.id)
    # Shown: courses the student actively takes (and that are still running), plus any
    # course where they have attendance history (dropped or retired: history only).
    active_ids = {
        course_id
        for (course_id,) in db.session.query(Enrollment.course_id)
        .join(Course, Course.id == Enrollment.course_id)
        .filter(Enrollment.student_id == student.id, Enrollment.is_active.is_(True), Course.is_active.is_(True))
    }
    course_ids = active_ids | set(tallies)
    if not course_ids:
        return []
    open_by_course = {
        s.course_id: s
        for s in AttendanceSession.query.filter(
            AttendanceSession.course_id.in_(active_ids), AttendanceSession.status == SessionStatus.OPEN
        )
    } if active_ids else {}
    courses = Course.query.filter(Course.id.in_(course_ids)).order_by(Course.term.desc(), Course.code).all()
    return [
        CourseSummary(
            course=course,
            present=tallies.get(course.id, {}).get(AttendanceStatus.PRESENT, 0),
            absent=tallies.get(course.id, {}).get(AttendanceStatus.ABSENT, 0),
            enrolled=course.id in active_ids,
            open_session=open_by_course.get(course.id),
        )
        for course in courses
    ]


def course_detail(student, course_id, now=None):
    """(CourseSummary, [(session, record or None)]) for one of the student's courses, newest first.

    Includes a running class (record None if not checked in yet). Raises NotFound
    for courses the student never took, so nobody can look up other courses.
    """
    summaries = {s.course.id: s for s in course_summaries(student, now)}
    if not valid_id(course_id) or course_id not in summaries:
        raise NotFound("Course not found.")
    summary = summaries[course_id]

    rows = (
        db.session.query(AttendanceSession, AttendanceRecord)
        .join(AttendanceRecord, AttendanceRecord.session_id == AttendanceSession.id)
        .filter(AttendanceSession.course_id == course_id, AttendanceRecord.student_id == student.id)
        .order_by(AttendanceSession.session_date.desc(), AttendanceSession.started_at.desc())
        .all()
    )
    history = [(session, record) for session, record in rows]
    if summary.open_session and all(s.id != summary.open_session.id for s, _ in history):
        history.insert(0, (summary.open_session, None))
    return summary, history
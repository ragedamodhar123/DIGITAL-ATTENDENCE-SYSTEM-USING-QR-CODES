"""Attendance sessions: start, stop, auto-close, and filling in absentees.

Rules
- Only a faculty member assigned to the course starts or stops its attendance.
- One open session per course (also enforced by the database). Pressing "Start"
  again, or a co-teacher pressing it, returns the session that is already open.
- One open session per faculty member: you cannot run attendance in two
  courses at once (two QR screens would confuse students).
- A course needs at least one enrolled student to start attendance.
- Sessions close themselves SESSION_MAX_MINUTES after starting, in case a
  faculty member forgets. There is no background job: overdue sessions are
  closed whenever any session-related action runs, and by the
  `flask close-expired-sessions` command (for a scheduled task).
- Closing marks every enrolled, active student who has no record as absent
  ("auto_absent"). Totals are therefore fixed at closing time: students
  enrolled later do not appear as absent in old sessions.
"""
import secrets
from datetime import timedelta

from flask import current_app
from sqlalchemy import and_, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    Course,
    CourseFaculty,
    Enrollment,
    MarkMethod,
    PendingCheckin,
    SessionKind,
    SessionStatus,
    User,
)
from ..utils.timeutils import local_date, utcnow
from .common import valid_id
from .errors import Conflict, NotFound, PermissionDenied, ValidationFailed

QR_SECRET_BYTES = 32  # 64 hex characters, matches AttendanceSession.qr_secret


# ---- helpers ---------------------------------------------------------------

def _require_active_faculty(actor):
    if actor is None or not actor.is_active or not actor.is_faculty:
        raise PermissionDenied("Only faculty can run attendance.")


def teaches(faculty, course_id):
    return db.session.get(CourseFaculty, (course_id, faculty.id)) is not None


def _get_course_for_faculty(actor, course_id):
    course = db.session.get(Course, course_id) if valid_id(course_id) else None
    if course is None:
        raise NotFound("Course not found.")
    if not teaches(actor, course.id):
        raise PermissionDenied("You can only run attendance for courses you teach.")
    return course


def _get_session(session_id, lock=False):
    if not valid_id(session_id):
        raise NotFound("Attendance session not found.")
    query = AttendanceSession.query.filter_by(id=session_id)
    if lock:
        # Row lock: two "Stop" clicks (or two co-teachers) cannot close it twice at once.
        query = query.with_for_update()
    session = query.one_or_none()
    if session is None:
        raise NotFound("Attendance session not found.")
    return session


def active_enrolment_count(course_id):
    return (
        db.session.query(func.count(Enrollment.id))
        .join(User, User.id == Enrollment.student_id)
        .filter(Enrollment.course_id == course_id, Enrollment.is_active.is_(True), User.is_active.is_(True))
        .scalar()
    )


# ---- closing ---------------------------------------------------------------

def _close(session, ended_at):
    """Close an open session (caller holds the row lock and commits)."""
    session.status = SessionStatus.CLOSED
    session.ended_at = max(ended_at, session.started_at)

    # Everyone actively enrolled (with an active account) who has no record yet is absent.
    # INSERT ... SELECT in one statement; ON CONFLICT skips anyone marked present at
    # the very last moment by a check-in finishing at the same time.
    absentees = (
        select(
            literal(session.id),
            Enrollment.student_id,
            literal("student"),
            literal(AttendanceStatus.ABSENT),
            literal(MarkMethod.AUTO_ABSENT),
            literal(session.ended_at),
        )
        .join(User, User.id == Enrollment.student_id)
        .where(
            Enrollment.course_id == session.course_id,
            Enrollment.is_active.is_(True),
            User.is_active.is_(True),
        )
    )
    db.session.execute(
        insert(AttendanceRecord)
        .from_select(["session_id", "student_id", "student_role", "status", "method", "marked_at"], absentees)
        .on_conflict_do_nothing(constraint="uq_attendance_records_session_id_student_id")
    )
    # Unfinished scans can no longer complete.
    PendingCheckin.query.filter_by(session_id=session.id).delete(synchronize_session=False)


def close_expired_sessions(now=None):
    """Close every open session past its expiry time. Returns how many were closed.

    Each is closed at its own expiry time (not "now"), so the record shows when
    attendance really stopped being possible.
    """
    now = now or utcnow()
    overdue = (
        AttendanceSession.query.filter(
            AttendanceSession.status == SessionStatus.OPEN, AttendanceSession.expires_at <= now
        )
        .with_for_update(skip_locked=True)  # another request already closing one? skip it
        .all()
    )
    for session in overdue:
        _close(session, session.expires_at)
    if overdue:
        db.session.commit()
        current_app.logger.info("auto-closed %d expired attendance session(s)", len(overdue))
    return len(overdue)


# ---- start / stop ----------------------------------------------------------

def open_session_for_course(course_id, now=None):
    """The course's open session, or None (expired ones are closed first)."""
    close_expired_sessions(now)
    return AttendanceSession.query.filter_by(course_id=course_id, status=SessionStatus.OPEN).one_or_none()


def start_session(actor, course_id, now=None):
    """Start attendance for a course. Returns (session, created).

    If the course already has an open session, that session is returned with
    created=False, so a double click or a co-teacher never makes a second one.
    """
    _require_active_faculty(actor)
    now = now or utcnow()
    course = _get_course_for_faculty(actor, course_id)
    if not course.is_active:
        raise ValidationFailed("This course is retired.", details={"_body": ["Attendance cannot be taken for a retired course."]})

    existing = open_session_for_course(course.id, now)
    if existing is not None:
        return existing, False

    elsewhere = (
        AttendanceSession.query.join(Course, Course.id == AttendanceSession.course_id)
        .filter(AttendanceSession.created_by_id == actor.id, AttendanceSession.status == SessionStatus.OPEN)
        .first()
    )
    if elsewhere is not None:
        raise Conflict(
            "Attendance is already running in another course.",
            details={"_body": [f"Stop attendance for {elsewhere.course.code} first."]},
        )

    if active_enrolment_count(course.id) == 0:
        raise ValidationFailed(
            "No students are enrolled.", details={"_body": ["Ask the admin to enrol students before taking attendance."]}
        )

    session = AttendanceSession(
        course=course,
        created_by=actor,
        kind=SessionKind.LIVE,
        status=SessionStatus.OPEN,
        session_date=local_date(now),
        started_at=now,
        expires_at=now + timedelta(minutes=current_app.config["SESSION_MAX_MINUTES"]),
        qr_secret=secrets.token_hex(QR_SECRET_BYTES),
    )
    db.session.add(session)
    try:
        db.session.commit()
    except IntegrityError as exc:
        # A co-teacher started it in the same instant: the database kept only one.
        db.session.rollback()
        if "uq_attendance_sessions_one_open_per_course" not in str(exc.orig):
            raise
        existing = AttendanceSession.query.filter_by(course_id=course.id, status=SessionStatus.OPEN).one()
        return existing, False

    current_app.logger.info("attendance started: session %s course %s by %s", session.id, course.code, actor.username)
    return session, True


def stop_session(actor, session_id, now=None):
    """Stop attendance and mark everyone not present as absent. Safe to call twice."""
    _require_active_faculty(actor)
    now = now or utcnow()
    session = _get_session(session_id, lock=True)
    if not teaches(actor, session.course_id):
        db.session.rollback()
        raise PermissionDenied("You can only stop attendance for courses you teach.")
    if session.status == SessionStatus.CLOSED:
        db.session.rollback()  # release the lock
        return session  # already closed (other teacher, auto-close, double click)

    # If it already expired, it really ended at its expiry time.
    _close(session, min(now, session.expires_at))
    db.session.commit()
    current_app.logger.info("attendance stopped: session %s by %s", session.id, actor.username)
    return session


# ---- reading ---------------------------------------------------------------

def get_session_for_faculty(actor, session_id, now=None):
    _require_active_faculty(actor)
    close_expired_sessions(now)
    session = _get_session(session_id)
    if not teaches(actor, session.course_id):
        raise PermissionDenied("You can only view attendance for courses you teach.")
    return session


def summary(session):
    """Counts for the faculty screen.

    Open session: expected = students enrolled right now.
    Closed session: everyone has a record, so the records are the truth.
    """
    counts = dict(
        db.session.query(AttendanceRecord.status, func.count(AttendanceRecord.id))
        .filter(AttendanceRecord.session_id == session.id)
        .group_by(AttendanceRecord.status)
        .all()
    )
    present = counts.get(AttendanceStatus.PRESENT, 0)
    if session.status == SessionStatus.OPEN:
        expected = active_enrolment_count(session.course_id)
        return {"expected": expected, "present": present, "not_yet": max(expected - present, 0), "absent": 0}
    absent = counts.get(AttendanceStatus.ABSENT, 0)
    return {"expected": present + absent, "present": present, "not_yet": 0, "absent": absent}
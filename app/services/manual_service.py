"""Manual attendance: faculty (or the admin) mark or change attendance, with a reason.

Rules
- Who: the admin, or a faculty member assigned to the course.
- Every change needs a reason (3-500 characters) and writes an audit row: old status,
  new status, reason, who, when. The audit table is append-only (database trigger),
  so the history cannot be rewritten.
- Changing a record: status changes, method becomes "manual", marked_by/marked_at are
  updated. The original face-check evidence (face_distance) is kept.
- New records (a student with no record yet) only for students actively enrolled with
  an active account. Existing records can be corrected even if the student has since
  been dropped or deactivated (fixing history).
- "Any date": for a date with no live session, a closed "manual" session is created
  and every actively enrolled student gets a record (ticked = present, others absent).
  Future dates are refused. If the course already has a session on that date, the
  caller must confirm (two lectures on one day are possible; a double submit is not).
- Works on open sessions too: a student who failed the face check 3 times can be
  marked present during class; a manual "absent" blocks later scanning.

Lock order, the same everywhere (session_service, attendance_service, here):
session row, then pending check-in rows, then attendance record rows. Keeping one
order is what prevents deadlocks between a manual change and a face check that
finish at the same moment (found and fixed with real parallel tests).
"""
from dataclasses import dataclass, field
from datetime import date

from flask import current_app
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (
    AttendanceAudit,
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    Course,
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
from .session_service import close_expired_sessions, teaches

REASON_MIN, REASON_MAX = 3, 500
EARLIEST_DATE = date(2000, 1, 1)


# ---- permission and lookups --------------------------------------------------

def can_edit(actor, course_id):
    if actor is None or not actor.is_active:
        return False
    if actor.is_admin:
        return True
    return actor.is_faculty and teaches(actor, course_id)


def _require_edit(actor, course_id):
    if not can_edit(actor, course_id):
        raise PermissionDenied("You can only change attendance for courses you teach.")


def _clean_reason(reason):
    text = reason.strip() if isinstance(reason, str) else ""
    if not REASON_MIN <= len(text) <= REASON_MAX:
        raise ValidationFailed(
            "Give a reason.", details={"reason": [f"Say why attendance is being changed ({REASON_MIN}-{REASON_MAX} characters)."]}
        )
    return text


def _clean_status(status):
    if status not in AttendanceStatus.ALL:
        raise ValidationFailed("Unknown status.", details={"status": ["Choose present or absent."]})
    return status


def _get_session(session_id, lock=False):
    if not valid_id(session_id):
        raise NotFound("Attendance session not found.")
    query = AttendanceSession.query.filter_by(id=session_id)
    if lock:
        query = query.with_for_update(read=True)  # shared: Stop (exclusive) waits for us
    session = query.one_or_none()
    if session is None:
        raise NotFound("Attendance session not found.")
    return session


def active_students(course_id):
    """Students actively enrolled in the course with an active account, by username."""
    return {
        user.username: user
        for user in User.query.join(Enrollment, Enrollment.student_id == User.id)
        .filter(Enrollment.course_id == course_id, Enrollment.is_active.is_(True), User.is_active.is_(True))
    }


# ---- changing records ----------------------------------------------------------

@dataclass
class ChangeSummary:
    changed: list = field(default_factory=list)    # usernames whose status changed
    created: list = field(default_factory=list)    # usernames that got a first record
    unchanged: list = field(default_factory=list)  # already had that status: nothing done


def set_statuses(actor, session_id, changes, reason, now=None):
    """Set several students' status in one session, with one reason. All or nothing.

    changes: {username: "present" | "absent"}. Students already at that status are
    reported as unchanged (no audit row: nothing changed).
    """
    now = now or utcnow()
    close_expired_sessions(now)
    reason = _clean_reason(reason)
    if not isinstance(changes, dict) or not changes:
        raise ValidationFailed("Nothing to change.", details={"changes": ["Choose at least one student."]})
    changes = {name: _clean_status(status) for name, status in changes.items()}

    session = _get_session(session_id, lock=True)
    try:
        _require_edit(actor, session.course_id)

        users = {u.username: u for u in User.query.filter(User.username.in_(list(changes)))}
        student_ids = [u.id for u in users.values()]
        # Lock order: session, then pending check-ins, then records (same as the face
        # check). A face check finishing right now holds its pending check-in; we wait
        # for it here, then see its record below instead of deadlocking with it.
        if student_ids:
            PendingCheckin.query.filter(
                PendingCheckin.session_id == session.id, PendingCheckin.student_id.in_(student_ids)
            ).with_for_update().all()
        records = {
            r.student_id: r
            for r in AttendanceRecord.query.filter(
                AttendanceRecord.session_id == session.id,
                AttendanceRecord.student_id.in_(student_ids),
            ).with_for_update()
        }
        active = active_students(session.course_id)

        errors = {}
        for name in changes:
            user = users.get(name)
            if user is None or not user.is_student:
                errors[name] = ["No student with this username."]
            elif user.id not in records and name not in active:
                errors[name] = ["Not actively enrolled in this course, so no record can be added."]
        if errors:
            raise ValidationFailed(
                f"Nothing was changed. Fix the {len(errors)} student(s) listed.", details={"changes": errors}
            )

        summary = ChangeSummary()
        for name, new_status in sorted(changes.items()):
            user = users[name]
            record = records.get(user.id)
            if record is None:
                record = AttendanceRecord(
                    session=session, student=user, status=new_status, method=MarkMethod.MANUAL,
                    marked_by=actor, marked_at=now,
                )
                db.session.add(record)
                db.session.flush()
                old_status = None
                summary.created.append(name)
            elif record.status == new_status:
                summary.unchanged.append(name)
                continue
            else:
                old_status = record.status
                record.status = new_status
                record.method = MarkMethod.MANUAL
                record.marked_by = actor
                record.marked_at = now
                summary.changed.append(name)
            db.session.add(
                AttendanceAudit(record=record, old_status=old_status, new_status=new_status, reason=reason, changed_by=actor)
            )

        # A manual decision ends any face check still in progress for these students.
        touched = [users[n].id for n in summary.changed + summary.created]
        if touched:
            PendingCheckin.query.filter(
                PendingCheckin.session_id == session.id, PendingCheckin.student_id.in_(touched)
            ).delete(synchronize_session=False)
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        if "uq_attendance_records_session_id_student_id" not in str(exc.orig):
            raise
        # A student's face check finished at the same moment as this manual change.
        raise Conflict(
            "Attendance was just recorded for one of these students.",
            details={"_body": ["A student checked in at the same moment. Reload the page and try again."]},
        ) from exc
    except Exception:
        db.session.rollback()
        raise

    current_app.logger.info(
        "manual attendance in session %s by %s: %d changed, %d added",
        session.id, actor.username, len(summary.changed), len(summary.created),
    )
    return summary


def set_status(actor, session_id, username, status, reason, now=None):
    """One student. Returns the ChangeSummary."""
    return set_statuses(actor, session_id, {username: status}, reason, now)


# ---- attendance for a date with no live session ------------------------------

def create_manual_session(actor, course_id, session_date, present_usernames, reason, allow_same_day=False, now=None):
    """Record attendance for a past (or today's) date without a live session.

    Every actively enrolled student gets a record: present if listed, otherwise absent.
    Returns (session, ChangeSummary).
    """
    now = now or utcnow()
    reason = _clean_reason(reason)
    course = db.session.get(Course, course_id) if valid_id(course_id) else None
    if course is None:
        raise NotFound("Course not found.")
    _require_edit(actor, course.id)
    if not course.is_active:
        raise ValidationFailed("This course is retired.", details={"_body": ["Attendance cannot be changed for a retired course."]})

    if not isinstance(session_date, date):
        raise ValidationFailed("Choose a date.", details={"session_date": ["Choose a date."]})
    today = local_date(now)
    if session_date > today:
        raise ValidationFailed("Date is in the future.", details={"session_date": ["You cannot record attendance for a future date."]})
    if session_date < EARLIEST_DATE:
        raise ValidationFailed("Date is too early.", details={"session_date": ["Choose a later date."]})

    if not allow_same_day:
        existing = AttendanceSession.query.filter_by(course_id=course.id, session_date=session_date).count()
        if existing:
            raise Conflict(
                "Attendance already exists for this date.",
                details={"session_date": [
                    f"{course.code} already has {existing} attendance record set(s) on this date. "
                    "Edit that instead, or confirm that this is another class on the same day."
                ]},
            )

    students = active_students(course.id)
    if not students:
        raise ValidationFailed("No students are enrolled.", details={"_body": ["Ask the admin to enrol students first."]})
    present = set(present_usernames or [])
    unknown = sorted(present - set(students))
    if unknown:
        raise ValidationFailed(
            "Unknown students.",
            details={"present": {name: ["Not actively enrolled in this course."] for name in unknown}},
        )

    session = AttendanceSession(
        course=course, created_by=actor, kind=SessionKind.MANUAL, status=SessionStatus.CLOSED,
        session_date=session_date, started_at=now, ended_at=now,
    )
    db.session.add(session)
    db.session.flush()

    summary = ChangeSummary()
    for name, user in sorted(students.items()):
        status = AttendanceStatus.PRESENT if name in present else AttendanceStatus.ABSENT
        record = AttendanceRecord(session=session, student=user, status=status, method=MarkMethod.MANUAL, marked_by=actor, marked_at=now)
        db.session.add(record)
        db.session.flush()
        db.session.add(AttendanceAudit(record=record, old_status=None, new_status=status, reason=reason, changed_by=actor))
        summary.created.append(name)
    db.session.commit()
    current_app.logger.info("manual session %s (%s %s) by %s", session.id, course.code, session_date, actor.username)
    return session, summary


# ---- reading -----------------------------------------------------------------

@dataclass
class RosterRow:
    student: User
    record: AttendanceRecord = None   # None = no record yet (open session: not checked in)
    changes: int = 0                  # how many audited manual changes this record has

    @property
    def status(self):
        return self.record.status if self.record else None


def session_roster(actor, session_id, now=None):
    """Every student of a session: those with a record, plus (for an open session)
    actively enrolled students who have not checked in yet. Sorted by username."""
    close_expired_sessions(now)
    session = _get_session(session_id)
    _require_edit(actor, session.course_id)

    records = AttendanceRecord.query.filter_by(session_id=session.id).all()
    counts = dict(
        db.session.query(AttendanceAudit.record_id, func.count(AttendanceAudit.id))
        .filter(AttendanceAudit.record_id.in_([r.id for r in records]))
        .group_by(AttendanceAudit.record_id)
    ) if records else {}
    rows = {r.student.username: RosterRow(r.student, r, counts.get(r.id, 0)) for r in records}
    if session.status == SessionStatus.OPEN:
        for name, user in active_students(session.course_id).items():
            rows.setdefault(name, RosterRow(user))
    return session, [rows[name] for name in sorted(rows)]


def record_history(actor, session_id, username):
    """Audit rows for one student's record in a session, oldest first."""
    session = _get_session(session_id)
    _require_edit(actor, session.course_id)
    record = (
        AttendanceRecord.query.join(User, User.id == AttendanceRecord.student_id)
        .filter(AttendanceRecord.session_id == session.id, User.username == username)
        .one_or_none()
    )
    if record is None:
        raise NotFound(f"{username} has no attendance record in this session.")
    return record, AttendanceAudit.query.filter_by(record_id=record.id).order_by(AttendanceAudit.changed_at, AttendanceAudit.id).all()


def list_sessions(actor, course_id):
    """All sessions of a course, newest first, each with present/absent counts."""
    course = db.session.get(Course, course_id) if valid_id(course_id) else None
    if course is None:
        raise NotFound("Course not found.")
    _require_edit(actor, course.id)
    close_expired_sessions()
    sessions = (
        AttendanceSession.query.filter_by(course_id=course.id)
        .order_by(AttendanceSession.session_date.desc(), AttendanceSession.started_at.desc())
        .all()
    )
    tallies = {}
    if sessions:
        for session_id, status, count in (
            db.session.query(AttendanceRecord.session_id, AttendanceRecord.status, func.count(AttendanceRecord.id))
            .filter(AttendanceRecord.session_id.in_([s.id for s in sessions]))
            .group_by(AttendanceRecord.session_id, AttendanceRecord.status)
        ):
            tallies.setdefault(session_id, {})[status] = count
    return course, [(s, tallies.get(s.id, {}).get("present", 0), tallies.get(s.id, {}).get("absent", 0)) for s in sessions]
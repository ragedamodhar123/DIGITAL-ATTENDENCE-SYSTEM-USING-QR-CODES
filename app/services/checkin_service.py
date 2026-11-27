"""Student check-in: the step between scanning the QR code and the face check.

Order of checks for a scan:
  1. the code is genuine and current, and its session is still open (qr_service)
  2. optional: the phone is on an allowed network (ATTENDANCE_ALLOWED_NETWORKS)
  3. the student is actively enrolled in the session's course
  4. the student's face is registered
  5. the student has no attendance record in this session yet
Then a short-lived "pending check-in" is created (PENDING_CHECKIN_SECONDS, 60 s):
the face check (Module 4, part 4) must finish within that time.

Every scan, accepted or not, is written to scan_logs (who, which session, what
happened, IP, browser), so suspicious patterns can be reviewed later.
"""
import ipaddress
import secrets
from dataclasses import dataclass
from datetime import timedelta

from flask import current_app
from sqlalchemy.dialects.postgresql import insert

from ..extensions import db
from ..models import (
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    Course,
    Enrollment,
    PendingCheckin,
    ScanLog,
    SessionStatus,
)
from ..utils.timeutils import utcnow
from . import qr_service
from .errors import ServiceError

MAX_USER_AGENT = 255


class CheckinRefused(ServiceError):
    """The scan was understood but cannot lead to attendance. `reason` is stable for the page."""

    status_code = 403
    code = "checkin_refused"

    MESSAGES = {
        "not_enrolled": "You are not enrolled in this course, so you cannot mark attendance for it.",
        "face_not_registered": "Register your face before marking attendance.",
        "outside_network": "Connect to the college Wi-Fi to mark attendance.",
        "already_recorded": "Your attendance for this class has already been recorded by your faculty.",
    }

    def __init__(self, reason):
        self.reason = reason
        super().__init__(self.MESSAGES[reason])

    def to_dict(self):
        body = super().to_dict()
        body["reason"] = self.reason
        return body


@dataclass
class CheckinResult:
    status: str                     # "ready" (go to face check) or "already_present"
    session: AttendanceSession
    course: Course
    expires_at: object = None       # when the pending check-in runs out (for "ready")
    challenge: str = None           # head-turn order for the face check: "LR" or "RL"


# ---- logging ---------------------------------------------------------------

def log_scan(user, outcome, session_id=None, detail=None, ip=None, user_agent=None, event="scan"):
    """Write and commit one scan_logs row, so it is kept even when the scan was refused.

    Refusals happen before anything else is written, so there is no unfinished
    work to undo here.
    """
    db.session.add(
        ScanLog(
            session_id=session_id,
            user_id=user.id,
            event=event,
            outcome=outcome[:32],
            detail=(detail or None) and detail[:255],
            ip_address=(ip or None) and ip[:45],
            user_agent=(user_agent or None) and user_agent[:MAX_USER_AGENT],
        )
    )
    db.session.commit()


# ---- checks ----------------------------------------------------------------

def on_allowed_network(ip):
    """True if no network rule is configured, or the IP is inside an allowed range."""
    networks = current_app.config.get("ATTENDANCE_ALLOWED_NETWORKS") or []
    if not networks:
        return True
    try:
        address = ipaddress.ip_address(ip)
    except (TypeError, ValueError):
        return False
    return any(address in ipaddress.ip_network(net, strict=False) for net in networks)


def _actively_enrolled(student, course_id):
    return (
        Enrollment.query.filter_by(course_id=course_id, student_id=student.id, is_active=True).first() is not None
    )


def begin_checkin(student, token, ip=None, user_agent=None, now=None):
    """Check a scanned code for this student. Returns CheckinResult, or raises
    TokenProblem / CheckinRefused. Logs the outcome either way."""
    now = now or utcnow()
    session_id = None
    try:
        session = qr_service.verify_token(token, now)
        session_id = session.id

        if not on_allowed_network(ip):
            raise CheckinRefused("outside_network")
        if not _actively_enrolled(student, session.course_id):
            raise CheckinRefused("not_enrolled")
        if student.face is None:
            raise CheckinRefused("face_not_registered")

        record = AttendanceRecord.query.filter_by(session_id=session.id, student_id=student.id).one_or_none()
        if record is not None:
            if record.status == AttendanceStatus.PRESENT:
                log_scan(student, "already_present", session_id, ip=ip, user_agent=user_agent)
                return CheckinResult("already_present", session, session.course)
            raise CheckinRefused("already_recorded")

        expires_at = now + timedelta(seconds=current_app.config["PENDING_CHECKIN_SECONDS"])
        # A fresh random head-turn order for every scan, so a pre-recorded video
        # would have to match an order picked seconds ago.
        challenge = secrets.choice(("LR", "RL"))
        # One pending check-in per student per session. Scanning again refreshes its
        # time and challenge, but keeps the number of face attempts already used, so
        # rescanning cannot be used to get unlimited face attempts.
        db.session.execute(
            insert(PendingCheckin)
            .values(
                session_id=session.id, student_id=student.id, student_role="student",
                created_at=now, expires_at=expires_at, challenge=challenge,
            )
            .on_conflict_do_update(
                constraint="uq_pending_checkins_session_id_student_id",
                set_={"created_at": now, "expires_at": expires_at, "challenge": challenge},
            )
        )
        db.session.commit()
    except (qr_service.TokenProblem, CheckinRefused) as problem:
        known_session = session_id or getattr(problem, "session_id", None)
        log_scan(student, problem.reason, known_session, ip=ip, user_agent=user_agent)
        raise

    log_scan(student, "ok", session.id, ip=ip, user_agent=user_agent)
    return CheckinResult("ready", session, session.course, expires_at, challenge)


def active_pending_checkin(student, session_id, now=None):
    """The student's unexpired pending check-in for this open session, or None."""
    now = now or utcnow()
    return (
        PendingCheckin.query.join(AttendanceSession, AttendanceSession.id == PendingCheckin.session_id)
        .filter(
            PendingCheckin.session_id == session_id,
            PendingCheckin.student_id == student.id,
            PendingCheckin.expires_at > now,
            AttendanceSession.status == SessionStatus.OPEN,
        )
        .one_or_none()
    )


def open_sessions_for_student(student, now=None):
    """Courses the student is enrolled in that have attendance open right now (for the home page)."""
    from .session_service import close_expired_sessions

    close_expired_sessions(now)
    return (
        AttendanceSession.query.join(Course, Course.id == AttendanceSession.course_id)
        .join(Enrollment, Enrollment.course_id == Course.id)
        .filter(
            Enrollment.student_id == student.id,
            Enrollment.is_active.is_(True),
            AttendanceSession.status == SessionStatus.OPEN,
        )
        .order_by(Course.code)
        .all()
    )
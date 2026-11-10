"""The last step of attendance: the face check, then marking the student present.

Flow: scan (checkin_service) -> pending check-in (60 s) -> this face check.

Guarantees
- A student is marked present at most once per session: the database's unique
  rule on (session, student), plus row locks so two taps cannot both succeed.
- Only the logged-in student's OWN registered face can match. A friend using
  their own phone and login with your face simply fails the check.
- At most FACE_CHECK_ATTEMPTS (3) mismatches per check-in. Rescanning the code does
  not reset the count. After that the student asks the faculty (manual marking).
- A photo that cannot be checked (too dark, no face, ...) does not use up an
  attempt: nothing was compared.
- If attendance stops or the 60 seconds run out during the check, nothing is marked.

Lock order (avoids deadlocks with stop_session, which locks the session row and
then deletes pending check-ins): session row first (shared lock), then the
pending check-in row.
"""
from dataclasses import dataclass

from flask import current_app
from sqlalchemy.dialects.postgresql import insert

from ..extensions import db
from ..models import (
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    MarkMethod,
    PendingCheckin,
    SessionStatus,
)
from ..utils.timeutils import utcnow
from . import face_service
from .checkin_service import log_scan
from .common import valid_id
from .errors import ServiceError


class FaceCheckRefused(ServiceError):
    """The face check cannot go ahead (or did not match). `reason` is stable for the page."""

    status_code = 409
    code = "face_check_refused"

    MESSAGES = {
        "no_checkin": "Scan the attendance code first.",
        "checkin_expired": "Time ran out. Scan the code on the screen again.",
        "session_closed": "Attendance for this class has ended.",
        "too_many_attempts": "Your face did not match after several tries. Ask your faculty to mark your attendance.",
        "already_recorded": "Your attendance for this class has already been recorded by your faculty.",
        "face_mismatch": "Your face did not match your registration. Face the camera in good light and try again.",
    }
    STATUS = {"too_many_attempts": 403, "face_mismatch": 422, "session_closed": 410}

    def __init__(self, reason, attempts_left=None):
        self.reason = reason
        self.attempts_left = attempts_left
        self.status_code = self.STATUS.get(reason, 409)
        super().__init__(self.MESSAGES[reason])

    def to_dict(self):
        body = super().to_dict()
        body["reason"] = self.reason
        if self.attempts_left is not None:
            body["attempts_left"] = self.attempts_left
        return body


@dataclass
class MarkResult:
    status: str  # "present" (just marked) or "already_present"
    record: AttendanceRecord


def _log(student, session_id, outcome, detail=None, ip=None, user_agent=None):
    log_scan(student, outcome, session_id, detail=detail, ip=ip, user_agent=user_agent, event="face")


def complete_checkin(student, session_id, image_data, ip=None, user_agent=None, now=None, engine=None):
    """Check the student's face and mark them present. Returns MarkResult or raises
    FaceCheckRefused / face_service.FaceProblem / FaceServiceUnavailable."""
    now = now or utcnow()
    max_attempts = current_app.config["FACE_CHECK_ATTEMPTS"]
    known_session = session_id if valid_id(session_id) else None

    def refuse(reason, attempts_left=None, detail=None):
        db.session.rollback()  # release the row locks before logging
        _log(student, known_session, reason, detail, ip, user_agent)
        raise FaceCheckRefused(reason, attempts_left)

    if known_session is None:
        refuse("no_checkin")

    # 1. Shared lock on the session: stop_session (exclusive lock) waits for us, or we wait for it.
    session = AttendanceSession.query.filter_by(id=session_id).with_for_update(read=True).one_or_none()
    if session is None:
        known_session = None
        refuse("no_checkin")
    if session.status != SessionStatus.OPEN or session.expires_at <= now:
        refuse("session_closed")

    # 2. Already marked (e.g. the first of two taps succeeded): report success again.
    record = AttendanceRecord.query.filter_by(session_id=session.id, student_id=student.id).one_or_none()
    if record is not None:
        if record.status == AttendanceStatus.PRESENT:
            PendingCheckin.query.filter_by(session_id=session.id, student_id=student.id).delete()
            db.session.commit()
            return MarkResult("already_present", record)
        refuse("already_recorded")

    # 3. The pending check-in, locked: a second tap waits here until the first finishes.
    pending = (
        PendingCheckin.query.filter_by(session_id=session.id, student_id=student.id).with_for_update().one_or_none()
    )
    if pending is None:
        # A tap that waited on the lock while another tap succeeded finds the check-in
        # used up: look again for the record before saying "scan first".
        record = AttendanceRecord.query.filter_by(session_id=session.id, student_id=student.id).one_or_none()
        if record is not None and record.status == AttendanceStatus.PRESENT:
            db.session.commit()
            return MarkResult("already_present", record)
        refuse("no_checkin")
    if pending.expires_at <= now:
        refuse("checkin_expired")
    if pending.face_attempts >= max_attempts:
        refuse("too_many_attempts", attempts_left=0)

    # 4. The face check itself. Photo problems (too dark, no face...) do not use an attempt.
    try:
        result = face_service.verify(student, image_data, engine)
    except face_service.FaceProblem as problem:
        db.session.rollback()
        _log(student, session.id, f"photo_{problem.reason}"[:32], None, ip, user_agent)
        raise
    except face_service.FaceNotRegistered:
        db.session.rollback()
        _log(student, session.id, "face_not_registered", None, ip, user_agent)
        raise

    detail = f"similarity={result.similarity:.3f}"
    if not result.matched:
        pending.face_attempts += 1
        attempts_left = max_attempts - pending.face_attempts
        db.session.commit()
        _log(student, session.id, "face_mismatch", detail, ip, user_agent)
        if attempts_left <= 0:
            raise FaceCheckRefused("too_many_attempts", attempts_left=0)
        raise FaceCheckRefused("face_mismatch", attempts_left=attempts_left)

    # 5. Match: mark present. ON CONFLICT is a final safety net behind the locks.
    inserted = db.session.execute(
        insert(AttendanceRecord)
        .values(
            session_id=session.id,
            student_id=student.id,
            student_role="student",
            status=AttendanceStatus.PRESENT,
            method=MarkMethod.QR_FACE,
            marked_at=now,
            face_distance=result.distance,
        )
        .on_conflict_do_nothing(constraint="uq_attendance_records_session_id_student_id")
        .returning(AttendanceRecord.id)
    ).scalar()
    db.session.delete(pending)
    db.session.commit()

    record = AttendanceRecord.query.filter_by(session_id=session.id, student_id=student.id).one()
    _log(student, session.id, "matched" if inserted else "already_present", detail, ip, user_agent)
    return MarkResult("present" if inserted else "already_present", record)
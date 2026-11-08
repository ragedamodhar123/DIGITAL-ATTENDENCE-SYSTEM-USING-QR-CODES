"""Attendance sessions, check-ins, records, audit trail and scan log.

Deletion rule (same as academic.py): RESTRICT everywhere, and
passive_deletes="all" on parent relationships so PostgreSQL decides.
"""
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, func, text

from ..extensions import db
from .base import TimestampMixin


class SessionKind:
    LIVE = "live"      # started by faculty, uses rotating QR + face check
    MANUAL = "manual"  # created when faculty marks attendance for a date with no live session
    ALL = (LIVE, MANUAL)


class SessionStatus:
    OPEN = "open"
    CLOSED = "closed"
    ALL = (OPEN, CLOSED)


class AttendanceStatus:
    PRESENT = "present"
    ABSENT = "absent"
    ALL = (PRESENT, ABSENT)


class MarkMethod:
    QR_FACE = "qr_face"          # student scanned and passed the face check
    MANUAL = "manual"            # faculty marked or edited it (always audited)
    AUTO_ABSENT = "auto_absent"  # filled in when the session closed
    ALL = (QR_FACE, MANUAL, AUTO_ABSENT)


class AttendanceSession(TimestampMixin, db.Model):
    """One class meeting for one course."""

    __tablename__ = "attendance_sessions"
    __table_args__ = (
        CheckConstraint("kind IN ('live', 'manual')", name="valid_kind"),
        CheckConstraint("status IN ('open', 'closed')", name="valid_status"),
        # Closed sessions always have an end time; open ones never do.
        CheckConstraint("(status = 'closed') = (ended_at IS NOT NULL)", name="ended_at_matches_status"),
        # Manual sessions are records of the past, so they are born closed.
        CheckConstraint("kind <> 'manual' OR status = 'closed'", name="manual_is_closed"),
        # Only live sessions have a QR secret.
        CheckConstraint("(kind = 'live') = (qr_secret IS NOT NULL)", name="qr_secret_only_for_live"),
        CheckConstraint("kind <> 'live' OR expires_at IS NOT NULL", name="live_has_expiry"),
        CheckConstraint("expires_at IS NULL OR expires_at > started_at", name="expiry_after_start"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="end_after_start"),
        # At most ONE open session per course. A second click on "Start Attendance"
        # (or two faculty of the same course) cannot create a duplicate.
        Index(
            "uq_attendance_sessions_one_open_per_course",
            "course_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_attendance_sessions_course_date", "course_id", "session_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id", ondelete="RESTRICT"), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    kind = db.Column(db.String(16), nullable=False)
    status = db.Column(db.String(16), nullable=False)
    # The class date in college local time (IST). Stored separately from the UTC
    # timestamps so "attendance on 5 Nov" never shifts because of time zones.
    session_date = db.Column(db.Date, nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = db.Column(db.DateTime(timezone=True))
    ended_at = db.Column(db.DateTime(timezone=True))
    # Random per-session key used to sign rotating QR tokens (Module 4).
    qr_secret = db.Column(db.String(64))

    course = db.relationship("Course")
    created_by = db.relationship("User")
    records = db.relationship("AttendanceRecord", back_populates="session", passive_deletes="all")

    def __repr__(self):
        return f"<AttendanceSession {self.id} course={self.course_id} {self.kind}/{self.status}>"


class PendingCheckin(db.Model):
    """A valid QR scan waiting for its face check. Short-lived (about 60 s).

    One row per (session, student): scanning again refreshes the same row.
    """

    __tablename__ = "pending_checkins"
    __table_args__ = (
        UniqueConstraint("session_id", "student_id"),
        ForeignKeyConstraint(["student_id", "student_role"], ["users.id", "users.role"], ondelete="RESTRICT"),
        CheckConstraint("student_role = 'student'", name="student_role_only"),
        CheckConstraint("face_attempts >= 0 AND face_attempts <= 10", name="face_attempts_range"),
        CheckConstraint("expires_at > created_at", name="expiry_after_create"),
    )

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer, db.ForeignKey("attendance_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    student_id = db.Column(db.Integer, nullable=False)
    student_role = db.Column(db.String(16), nullable=False, default="student", server_default="student")
    face_attempts = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)

    session = db.relationship("AttendanceSession")
    student = db.relationship("User")

    def __repr__(self):
        return f"<PendingCheckin session={self.session_id} student={self.student_id}>"


class AttendanceRecord(TimestampMixin, db.Model):
    """Present/absent for one student in one session. Exactly one row per pair."""

    __tablename__ = "attendance_records"
    __table_args__ = (
        # THE core rule: a student can be marked only once per session.
        # The service recognises a violation by this exact name.
        UniqueConstraint("session_id", "student_id"),
        ForeignKeyConstraint(["student_id", "student_role"], ["users.id", "users.role"], ondelete="RESTRICT"),
        CheckConstraint("student_role = 'student'", name="student_role_only"),
        CheckConstraint("status IN ('present', 'absent')", name="valid_status"),
        CheckConstraint("method IN ('qr_face', 'manual', 'auto_absent')", name="valid_method"),
        # A QR+face mark is always 'present' and must keep its face-match evidence.
        CheckConstraint(
            "method <> 'qr_face' OR (status = 'present' AND face_distance IS NOT NULL)",
            name="qr_face_is_present_with_evidence",
        ),
        CheckConstraint("method <> 'auto_absent' OR status = 'absent'", name="auto_absent_is_absent"),
        # A manual mark must say who made it.
        CheckConstraint("method <> 'manual' OR marked_by_id IS NOT NULL", name="manual_has_marker"),
        CheckConstraint("face_distance IS NULL OR face_distance >= 0", name="face_distance_non_negative"),
        Index("ix_attendance_records_student_id", "student_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer, db.ForeignKey("attendance_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    student_id = db.Column(db.Integer, nullable=False)
    student_role = db.Column(db.String(16), nullable=False, default="student", server_default="student")
    status = db.Column(db.String(16), nullable=False)
    method = db.Column(db.String(16), nullable=False)
    marked_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())
    marked_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"))
    # How close the live face was to the enrolled face (lower = more similar).
    # Kept as evidence even if the record is later edited manually.
    face_distance = db.Column(db.Float)

    session = db.relationship("AttendanceSession", back_populates="records")
    student = db.relationship("User", foreign_keys=[student_id, student_role])
    marked_by = db.relationship("User", foreign_keys=[marked_by_id])
    audits = db.relationship(
        "AttendanceAudit",
        back_populates="record",
        passive_deletes="all",
        order_by="AttendanceAudit.changed_at",
    )

    def __repr__(self):
        return f"<AttendanceRecord session={self.session_id} student={self.student_id} {self.status}>"


class AttendanceAudit(db.Model):
    """Every manual mark or edit: what changed, why, who, when.

    Append-only: a PostgreSQL trigger (created in the initial migration)
    blocks UPDATE and DELETE on this table, even from pgAdmin.
    """

    __tablename__ = "attendance_audit"
    __table_args__ = (
        CheckConstraint("old_status IS NULL OR old_status IN ('present', 'absent')", name="valid_old_status"),
        CheckConstraint("new_status IN ('present', 'absent')", name="valid_new_status"),
        # An "edit" that changes nothing is not an edit.
        CheckConstraint("old_status IS DISTINCT FROM new_status", name="status_actually_changes"),
        CheckConstraint("length(trim(reason)) >= 3", name="reason_required"),
        Index("ix_attendance_audit_record_id", "record_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(
        db.Integer, db.ForeignKey("attendance_records.id", ondelete="RESTRICT"), nullable=False
    )
    old_status = db.Column(db.String(16))  # NULL when the record was created manually
    new_status = db.Column(db.String(16), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    changed_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    changed_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())

    record = db.relationship("AttendanceRecord", back_populates="audits")
    changed_by = db.relationship("User")

    def __repr__(self):
        return f"<AttendanceAudit record={self.record_id} {self.old_status}->{self.new_status}>"


class ScanLog(db.Model):
    """Every scan and face attempt, successful or not, for fraud review.

    outcome is free text on purpose (e.g. ok, expired_token, not_enrolled,
    face_mismatch): new outcomes can be added without a migration.
    """

    __tablename__ = "scan_logs"
    __table_args__ = (
        CheckConstraint("event IN ('scan', 'face')", name="valid_event"),
        CheckConstraint("length(outcome) > 0", name="outcome_not_blank"),
        Index("ix_scan_logs_session_created", "session_id", "created_at"),
        Index("ix_scan_logs_user_created", "user_id", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # NULL when the QR token was too broken to tell which session it was for.
    session_id = db.Column(db.Integer, db.ForeignKey("attendance_sessions.id", ondelete="RESTRICT"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    event = db.Column(db.String(8), nullable=False)
    outcome = db.Column(db.String(32), nullable=False)
    detail = db.Column(db.String(255))
    ip_address = db.Column(db.String(45))  # 45 fits the longest IPv6 form
    user_agent = db.Column(db.String(255))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())

    def __repr__(self):
        return f"<ScanLog {self.event}:{self.outcome} user={self.user_id}>"
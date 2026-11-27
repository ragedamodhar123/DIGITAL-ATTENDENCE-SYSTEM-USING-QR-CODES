"""Module 0, part 3: attendance sessions, records, audit, check-ins, scan log."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.extensions import db
from app.models import (
    AttendanceAudit,
    AttendanceRecord,
    AttendanceSession,
    PendingCheckin,
    Role,
    ScanLog,
)
from tests.factories import make_course, make_record, make_session, make_user


def _expect_db_rejects(fragment=None):
    with pytest.raises(IntegrityError) as exc:
        db.session.flush()
    db.session.rollback()
    if fragment:
        assert fragment in str(exc.value)


@contextmanager
def db_rejects(fragment=None):
    """Use when the factory itself saves (flushes) the bad row."""
    with pytest.raises(IntegrityError) as exc:
        yield
    db.session.rollback()
    if fragment:
        assert fragment in str(exc.value)


def _now():
    return datetime.now(timezone.utc)


# ---- Sessions -----------------------------------------------------------

def test_live_session_defaults_and_relationships():
    session = make_session()
    db.session.refresh(session)
    assert session.kind == "live" and session.status == "open"
    assert session.course is not None and session.created_by is not None
    assert session.started_at.tzinfo is not None


def test_only_one_open_session_per_course():
    course = make_course()
    make_session(course=course)
    db.session.add(
        AttendanceSession(
            course=course, created_by=make_user(), kind="live", status="open",
            session_date=date.today(), started_at=_now(), qr_secret="cd" * 32,
            expires_at=_now() + timedelta(hours=1),
        )
    )
    _expect_db_rejects("uq_attendance_sessions_one_open_per_course")


def test_new_open_session_allowed_after_previous_closed():
    course = make_course()
    make_session(course=course, status="closed")
    make_session(course=course, status="closed")
    make_session(course=course, status="open")


def test_open_sessions_in_different_courses_allowed():
    make_session()
    make_session()


def test_closed_session_needs_end_time():
    with db_rejects("ended_at_matches_status"):
        make_session(status="closed", ended_at=None)


def test_open_session_cannot_have_end_time():
    with db_rejects("ended_at_matches_status"):
        make_session(status="open", ended_at=_now() + timedelta(hours=1))


def test_manual_session_must_be_closed():
    db.session.add(
        AttendanceSession(
            course=make_course(), created_by=make_user(), kind="manual",
            status="open", session_date=date(2026, 11, 2),
        )
    )
    _expect_db_rejects("manual_is_closed")


def test_manual_session_has_no_qr_secret():
    with db_rejects("qr_secret_only_for_live"):
        make_session(kind="manual", status="closed", qr_secret="ef" * 32)


def test_live_session_needs_qr_secret_and_expiry():
    with db_rejects("qr_secret_only_for_live"):
        make_session(qr_secret=None)
    with db_rejects("live_has_expiry"):
        make_session(expires_at=None)


def test_expiry_must_be_after_start():
    start = _now()
    with db_rejects("expiry_after_start"):
        make_session(started_at=start, expires_at=start - timedelta(seconds=1))


@pytest.mark.parametrize("field,value", [("kind", "zoom"), ("status", "paused")])
def test_invalid_kind_or_status(field, value):
    with db_rejects():
        make_session(**{field: value})


def test_manual_session_for_past_date():
    session = make_session(kind="manual", status="closed", session_date=date(2026, 10, 1))
    assert session.qr_secret is None and session.expires_at is None


# ---- Records: the one-mark-per-session rule -----------------------------

def test_student_marked_only_once_per_session():
    record = make_record()
    db.session.add(
        AttendanceRecord(
            session=record.session, student=record.student, status="present",
            method="qr_face", face_distance=0.3,
        )
    )
    _expect_db_rejects("uq_attendance_records_session_id_student_id")


def test_once_per_session_holds_across_separate_transactions():
    record = make_record()
    db.session.commit()
    session_id, student_id = record.session_id, record.student_id
    with pytest.raises(IntegrityError):
        db.session.execute(
            text(
                "INSERT INTO attendance_records (session_id, student_id, status, method, marked_by_id) "
                "VALUES (:s, :u, 'absent', 'manual', :u2)"
            ),
            {"s": session_id, "u": student_id, "u2": record.session.created_by_id},
        )
    db.session.rollback()


def test_same_student_in_different_sessions_allowed():
    student = make_user(role=Role.STUDENT)
    make_record(student=student)
    make_record(student=student)


@pytest.mark.parametrize("role", [Role.FACULTY, Role.ADMIN])
def test_only_students_can_have_attendance(role):
    with db_rejects():
        make_record(student=make_user(role=role))


def test_qr_face_mark_must_be_present_with_evidence():
    with db_rejects("qr_face_is_present_with_evidence"):
        make_record(method="qr_face", face_distance=None)
    with db_rejects("qr_face_is_present_with_evidence"):
        make_record(method="qr_face", status="absent")


def test_auto_absent_must_be_absent():
    with db_rejects("auto_absent_is_absent"):
        make_record(method="auto_absent", status="present")


def test_manual_mark_must_record_who():
    with db_rejects("manual_has_marker"):
        make_record(method="manual", status="present", marked_by_id=None)


def test_manual_edit_keeps_face_evidence():
    record = make_record(face_distance=0.31)
    record.status, record.method = "absent", "manual"
    record.marked_by = record.session.created_by
    db.session.flush()
    assert record.face_distance == 0.31


def test_negative_face_distance_rejected():
    with db_rejects("face_distance_non_negative"):
        make_record(face_distance=-0.1)


def test_cannot_delete_session_with_records():
    record = make_record()
    db.session.delete(record.session)
    _expect_db_rejects("foreign key")


# ---- Audit trail --------------------------------------------------------

def _audit(record, old="present", new="absent", reason="Left after roll call"):
    entry = AttendanceAudit(
        record=record, old_status=old, new_status=new, reason=reason,
        changed_by=record.session.created_by,
    )
    db.session.add(entry)
    return entry


def test_audit_entry_saved_with_who_and_when():
    record = make_record()
    entry = _audit(record)
    db.session.flush()
    db.session.refresh(entry)
    assert entry.changed_at.tzinfo is not None
    assert entry.changed_by == record.session.created_by
    assert record.audits == [entry]


def test_manual_creation_audit_has_no_old_status():
    record = make_record(method="manual", status="present", marked_by=make_user())
    _audit(record, old=None, new="present", reason="Was present, phone battery dead")
    db.session.flush()


@pytest.mark.parametrize("reason", ["", "  ", "ok"])
def test_audit_requires_real_reason(reason):
    _audit(make_record(), reason=reason)
    _expect_db_rejects("reason_required")


def test_audit_rejects_no_op_change():
    _audit(make_record(), old="present", new="present")
    _expect_db_rejects("status_actually_changes")


def test_audit_rows_cannot_be_updated():
    entry = _audit(make_record())
    db.session.flush()
    entry.reason = "Rewritten history"
    with pytest.raises(DBAPIError) as exc:
        db.session.flush()
    db.session.rollback()
    assert "append-only" in str(exc.value)


def test_audit_rows_cannot_be_deleted_even_with_raw_sql():
    _audit(make_record())
    db.session.flush()
    with pytest.raises(DBAPIError) as exc:
        db.session.execute(text("DELETE FROM attendance_audit"))
    db.session.rollback()
    assert "append-only" in str(exc.value)


def test_cannot_delete_record_that_has_audit_history():
    record = make_record()
    _audit(record)
    db.session.flush()
    db.session.delete(record)
    _expect_db_rejects("foreign key")


# ---- Pending check-ins --------------------------------------------------

def _pending(session, student, **extra):
    values = {"session": session, "student": student, "expires_at": _now() + timedelta(seconds=60)}
    values.update(extra)
    row = PendingCheckin(**values)
    db.session.add(row)
    return row


def test_one_pending_checkin_per_student_per_session():
    session, student = make_session(), make_user(role=Role.STUDENT)
    _pending(session, student)
    db.session.flush()
    _pending(session, student)
    _expect_db_rejects("uq_pending_checkins_session_id_student_id")


def test_pending_checkin_defaults():
    row = _pending(make_session(), make_user(role=Role.STUDENT))
    db.session.flush()
    db.session.refresh(row)
    assert row.face_attempts == 0


def test_pending_checkin_only_for_students():
    _pending(make_session(), make_user(role=Role.FACULTY))
    _expect_db_rejects()


@pytest.mark.parametrize("attempts", [-1, 11])
def test_face_attempts_bounded(attempts):
    _pending(make_session(), make_user(role=Role.STUDENT), face_attempts=attempts)
    _expect_db_rejects("face_attempts_range")


def test_pending_expiry_must_be_after_creation():
    now = _now()
    _pending(make_session(), make_user(role=Role.STUDENT), created_at=now, expires_at=now)
    _expect_db_rejects("expiry_after_create")


# ---- Scan log -----------------------------------------------------------

def test_scan_log_allows_unknown_session():
    db.session.add(ScanLog(user_id=make_user(role=Role.STUDENT).id, event="scan", outcome="invalid_token"))
    db.session.flush()


def test_scan_log_rejects_unknown_event():
    db.session.add(ScanLog(user_id=make_user().id, event="login", outcome="ok"))
    _expect_db_rejects("valid_event")


def test_scan_log_rejects_blank_outcome():
    db.session.add(ScanLog(user_id=make_user().id, event="scan", outcome=""))
    _expect_db_rejects("outcome_not_blank")
"""Module 4, part 3: scanning a code and the pending check-in."""
import struct
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models import Enrollment, FaceEmbedding, PendingCheckin, Role, ScanLog
from app.services import checkin_service as svc
from app.services import qr_service, session_service
from app.services.checkin_service import CheckinRefused
from tests.factories import assign_faculty, enroll, make_course, make_record, make_user

T0 = datetime(2026, 11, 10, 4, 0, 0, tzinfo=timezone.utc)


def _face(student):
    db.session.add(FaceEmbedding(student=student, model_name="t", dim=128, embedding=struct.pack("<128f", *[0.0] * 128)))
    db.session.flush()


@pytest.fixture
def live():
    faculty = make_user(role=Role.FACULTY)
    course = make_course(code="DS301")
    assign_faculty(course, faculty)
    student = make_user(role=Role.STUDENT, username="stu001", must_change_password=False)
    enroll(course, student)
    _face(student)
    session, _ = session_service.start_session(faculty, course.id, now=T0)
    token, _ = qr_service.make_token(session, T0)
    return faculty, course, student, session, token


def _logs():
    return [(log.outcome, log.session_id) for log in ScanLog.query.order_by(ScanLog.id)]


def _refused(call):
    with pytest.raises((CheckinRefused, qr_service.TokenProblem)) as exc:
        call()
    return exc.value.reason


# ---- happy path --------------------------------------------------------------

def test_valid_scan_creates_pending_check_in_and_logs_it(live):
    _, course, student, session, token = live
    result = svc.begin_checkin(student, token, ip="10.0.0.5", user_agent="Phone/1.0", now=T0 + timedelta(seconds=3))
    assert result.status == "ready" and result.course == course
    pending = PendingCheckin.query.one()
    assert (pending.session_id, pending.student_id, pending.face_attempts) == (session.id, student.id, 0)
    assert pending.expires_at == T0 + timedelta(seconds=63)
    log = ScanLog.query.one()
    assert (log.outcome, log.session_id, log.ip_address, log.user_agent, log.event) == ("ok", session.id, "10.0.0.5", "Phone/1.0", "scan")


def test_scan_just_after_code_changes_still_counts(live):
    _, _, student, _, token = live
    assert svc.begin_checkin(student, token, now=T0 + timedelta(seconds=15)).status == "ready"


def test_rescanning_refreshes_time_but_keeps_face_attempts(live):
    _, _, student, session, token = live
    svc.begin_checkin(student, token, now=T0)
    pending = PendingCheckin.query.one()
    pending.face_attempts = 2  # two failed face checks so far
    db.session.commit()

    later = T0 + timedelta(seconds=30)
    svc.begin_checkin(student, qr_service.make_token(session, later)[0], now=later)
    db.session.expire_all()
    pending = PendingCheckin.query.one()
    assert pending.face_attempts == 2  # rescanning does not give fresh attempts
    assert pending.expires_at == later + timedelta(seconds=60)


def test_already_present_is_reported_not_refused(live):
    _, _, student, session, token = live
    make_record(session=session, student=student, status="present", method="qr_face")
    result = svc.begin_checkin(student, token, now=T0)
    assert result.status == "already_present"
    assert PendingCheckin.query.count() == 0
    assert _logs() == [("already_present", session.id)]


# ---- refusals --------------------------------------------------------------

def test_expired_code(live):
    _, _, student, session, token = live
    assert _refused(lambda: svc.begin_checkin(student, token, now=T0 + timedelta(seconds=25))) == "expired_code"
    assert _logs() == [("expired_code", session.id)]
    assert PendingCheckin.query.count() == 0


def test_garbage_code_is_logged_without_a_session(live):
    _, _, student, _, _ = live
    assert _refused(lambda: svc.begin_checkin(student, "not-a-code", now=T0)) == "invalid_code"
    assert _logs() == [("invalid_code", None)]


def test_code_after_attendance_stopped(live):
    faculty, _, student, session, token = live
    session_service.stop_session(faculty, session.id, now=T0 + timedelta(seconds=2))
    assert _refused(lambda: svc.begin_checkin(student, token, now=T0 + timedelta(seconds=3))) == "session_closed"
    assert _logs() == [("session_closed", session.id)]


def test_forged_code_never_names_a_session_in_the_log(live):
    _, _, student, session, token = live
    sid, window, sig = token.split(".")
    forged = f"{sid}.{window}.{'A' * 22}"
    assert _refused(lambda: svc.begin_checkin(student, forged, now=T0)) == "invalid_code"
    assert _logs() == [("invalid_code", None)]  # unproven codes are not tied to a session


def test_student_not_enrolled_in_that_course(live):
    *_, token = live
    stranger = make_user(role=Role.STUDENT, must_change_password=False)
    _face(stranger)
    assert _refused(lambda: svc.begin_checkin(stranger, token, now=T0)) == "not_enrolled"


def test_dropped_student_cannot_check_in(live):
    _, _, student, _, token = live
    Enrollment.query.filter_by(student_id=student.id).one().is_active = False
    assert _refused(lambda: svc.begin_checkin(student, token, now=T0)) == "not_enrolled"


def test_face_must_be_registered(live):
    _, course, _, _, token = live
    newcomer = make_user(role=Role.STUDENT, must_change_password=False)
    enroll(course, newcomer)
    assert _refused(lambda: svc.begin_checkin(newcomer, token, now=T0)) == "face_not_registered"


def test_record_made_by_faculty_blocks_check_in(live):
    faculty, _, student, session, token = live
    make_record(session=session, student=student, status="absent", method="manual", marked_by=faculty)
    assert _refused(lambda: svc.begin_checkin(student, token, now=T0)) == "already_recorded"


# ---- optional network rule ---------------------------------------------------

def test_network_rule(live, app, monkeypatch):
    _, _, student, _, token = live
    monkeypatch.setitem(app.config, "ATTENDANCE_ALLOWED_NETWORKS", ["203.0.113.0/24", "198.51.100.7"])
    assert _refused(lambda: svc.begin_checkin(student, token, ip="192.0.2.10", now=T0)) == "outside_network"
    assert _refused(lambda: svc.begin_checkin(student, token, ip=None, now=T0)) == "outside_network"
    assert svc.begin_checkin(student, token, ip="203.0.113.44", now=T0).status == "ready"


def test_no_network_rule_means_any_network(live):
    _, _, student, _, token = live
    assert svc.begin_checkin(student, token, ip="192.0.2.10", now=T0).status == "ready"


def test_invalid_network_setting_is_caught_at_startup(monkeypatch):
    from app import create_app
    from app.config import TestingConfig

    monkeypatch.setattr(TestingConfig, "ATTENDANCE_ALLOWED_NETWORKS", ["203.0.113.0/33"])
    with pytest.raises(RuntimeError, match="ATTENDANCE_ALLOWED_NETWORKS"):
        create_app("testing")


# ---- helpers ---------------------------------------------------------------

def test_pending_check_in_lookup(live):
    faculty, _, student, session, token = live
    svc.begin_checkin(student, token, now=T0)
    assert svc.active_pending_checkin(student, session.id, now=T0 + timedelta(seconds=59)) is not None
    assert svc.active_pending_checkin(student, session.id, now=T0 + timedelta(seconds=61)) is None


def test_stopping_attendance_cancels_pending_check_ins(live):
    faculty, _, student, session, token = live
    svc.begin_checkin(student, token, now=T0)
    session_service.stop_session(faculty, session.id, now=T0 + timedelta(seconds=5))
    assert svc.active_pending_checkin(student, session.id, now=T0 + timedelta(seconds=6)) is None


def test_open_sessions_shown_to_enrolled_students_only(live):
    _, course, student, session, _ = live
    assert [s.id for s in svc.open_sessions_for_student(student, now=T0)] == [session.id]
    stranger = make_user(role=Role.STUDENT)
    assert svc.open_sessions_for_student(stranger, now=T0) == []


def test_long_user_agent_is_trimmed(live):
    _, _, student, _, token = live
    svc.begin_checkin(student, token, user_agent="x" * 1000, now=T0)
    assert len(ScanLog.query.one().user_agent) == 255
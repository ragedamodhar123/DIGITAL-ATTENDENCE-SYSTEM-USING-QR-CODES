"""Module 4, part 4: face check and marking present."""
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models import AttendanceRecord, FaceEmbedding, PendingCheckin, Role, ScanLog
from app.services import attendance_service as svc
from app.services import checkin_service, face_service, qr_service, session_service
from app.services.attendance_service import FaceCheckRefused
from app.services.face_engine import EMBEDDING_DIM, MODEL_NAME
from tests.face_helpers import FakeEngine, encode, face, near, photo, unit
from tests.factories import assign_faculty, enroll, make_course, make_record, make_user

T0 = datetime(2026, 11, 10, 4, 0, 0, tzinfo=timezone.utc)
MY_FACE = unit(5)
PHOTO = encode(photo())


def register_face(student, vector=MY_FACE):
    db.session.add(FaceEmbedding(student=student, model_name=MODEL_NAME, dim=EMBEDDING_DIM, embedding=face_service.to_bytes(vector)))
    db.session.flush()


def me():
    return FakeEngine(embeddings=[near(MY_FACE, seed=11)])


def stranger():
    return FakeEngine(embeddings=[unit(77)])


@pytest.fixture
def checked_in():
    """A student who scanned a valid code at T0 (pending check-in until T0+60 s)."""
    faculty = make_user(role=Role.FACULTY)
    course = make_course(code="DS301")
    assign_faculty(course, faculty)
    student = make_user(role=Role.STUDENT, username="stu001", must_change_password=False)
    enroll(course, student)
    register_face(student)
    session, _ = session_service.start_session(faculty, course.id, now=T0)
    checkin_service.begin_checkin(student, qr_service.make_token(session, T0)[0], now=T0)
    return faculty, session, student


def _complete(student, session, engine, now=T0 + timedelta(seconds=10)):
    return svc.complete_checkin(student, session.id, PHOTO, ip="10.0.0.9", now=now, engine=engine)


def _refused(call):
    with pytest.raises(FaceCheckRefused) as exc:
        call()
    return exc.value


def _face_logs():
    return [l.outcome for l in ScanLog.query.filter_by(event="face").order_by(ScanLog.id)]


# ---- success -----------------------------------------------------------------

def test_matching_face_marks_present_with_evidence(checked_in):
    _, session, student = checked_in
    result = _complete(student, session, me())
    assert result.status == "present"
    record = AttendanceRecord.query.one()
    assert (record.status, record.method, record.student_id) == ("present", "qr_face", student.id)
    assert record.marked_at == T0 + timedelta(seconds=10)
    assert 0 <= record.face_distance < 0.5
    assert PendingCheckin.query.count() == 0       # check-in used up
    assert _face_logs() == ["matched"]
    assert "similarity=" in ScanLog.query.filter_by(event="face").one().detail


def test_second_tap_after_success_reports_already_present(checked_in):
    _, session, student = checked_in
    _complete(student, session, me())
    again = _complete(student, session, me())
    assert again.status == "already_present"
    assert AttendanceRecord.query.count() == 1


def test_faculty_count_includes_the_new_mark(checked_in):
    _, session, student = checked_in
    _complete(student, session, me())
    assert session_service.summary(session)["present"] == 1


# ---- mismatches and attempts -------------------------------------------------

def test_someone_elses_face_does_not_match(checked_in):
    _, session, student = checked_in
    problem = _refused(lambda: _complete(student, session, stranger()))
    assert (problem.reason, problem.attempts_left, problem.status_code) == ("face_mismatch", 2, 422)
    assert AttendanceRecord.query.count() == 0
    assert PendingCheckin.query.one().face_attempts == 1
    assert _face_logs() == ["face_mismatch"]


def test_three_mismatches_then_blocked_even_after_rescanning(checked_in):
    _, session, student = checked_in
    assert _refused(lambda: _complete(student, session, stranger())).attempts_left == 2
    assert _refused(lambda: _complete(student, session, stranger())).attempts_left == 1
    third = _refused(lambda: _complete(student, session, stranger()))
    assert (third.reason, third.attempts_left, third.status_code) == ("too_many_attempts", 0, 403)

    later = T0 + timedelta(seconds=20)
    checkin_service.begin_checkin(student, qr_service.make_token(session, later)[0], now=later)
    blocked = _refused(lambda: _complete(student, session, me(), now=later + timedelta(seconds=1)))
    assert blocked.reason == "too_many_attempts"  # even the right face: ask the faculty
    assert AttendanceRecord.query.count() == 0


def test_mismatch_then_match_succeeds(checked_in):
    _, session, student = checked_in
    _refused(lambda: _complete(student, session, stranger()))
    assert _complete(student, session, me()).status == "present"


def test_bad_photo_does_not_use_an_attempt(checked_in):
    _, session, student = checked_in
    with pytest.raises(face_service.FaceProblem) as exc:
        _complete(student, session, FakeEngine(detections=[]))
    assert exc.value.reason == "no_face"
    assert PendingCheckin.query.one().face_attempts == 0
    assert _face_logs() == ["photo_no_face"]


# ---- timing and state --------------------------------------------------------

def test_check_after_60_seconds_is_refused(checked_in):
    _, session, student = checked_in
    problem = _refused(lambda: _complete(student, session, me(), now=T0 + timedelta(seconds=61)))
    assert problem.reason == "checkin_expired"
    assert AttendanceRecord.query.count() == 0


def test_attendance_stopped_during_check(checked_in):
    faculty, session, student = checked_in
    session_service.stop_session(faculty, session.id, now=T0 + timedelta(seconds=5))
    problem = _refused(lambda: _complete(student, session, me()))
    assert (problem.reason, problem.status_code) == ("session_closed", 410)
    record = AttendanceRecord.query.filter_by(student_id=student.id).one()
    assert record.status == "absent"  # filled in at stop; not overwritten


def test_session_past_its_expiry(checked_in):
    _, session, student = checked_in
    problem = _refused(lambda: _complete(student, session, me(), now=session.expires_at + timedelta(seconds=1)))
    assert problem.reason == "session_closed"


def test_no_scan_first(checked_in):
    _, session, student = checked_in
    PendingCheckin.query.delete()
    db.session.flush()
    assert _refused(lambda: _complete(student, session, me())).reason == "no_checkin"


@pytest.mark.parametrize("bad_id", [0, 99999, None, True])
def test_unknown_session(checked_in, bad_id):
    _, _, student = checked_in
    with pytest.raises(FaceCheckRefused) as exc:
        svc.complete_checkin(student, bad_id, PHOTO, now=T0, engine=me())
    assert exc.value.reason == "no_checkin"


def test_another_students_check_in_cannot_be_completed(checked_in):
    # Friend B has no pending check-in of their own for this session.
    _, session, _ = checked_in
    friend = make_user(role=Role.STUDENT, must_change_password=False)
    register_face(friend, unit(9))
    db.session.commit()  # real accounts always exist in the database before they log in
    assert _refused(lambda: _complete(friend, session, FakeEngine(embeddings=[near(unit(9))]))).reason == "no_checkin"


def test_manual_record_blocks_face_check(checked_in):
    faculty, session, student = checked_in
    make_record(session=session, student=student, status="absent", method="manual", marked_by=faculty)
    assert _refused(lambda: _complete(student, session, me())).reason == "already_recorded"


def test_face_registered_with_another_model_is_refused(checked_in):
    _, session, student = checked_in
    student.face.model_name = "old-model"
    db.session.flush()
    with pytest.raises(face_service.FaceNotRegistered):
        _complete(student, session, me())
    assert _face_logs() == ["face_not_registered"]
"""Module 4, part 4 + liveness: face check with head-turn challenge, and marking present."""
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models import AttendanceRecord, FaceEmbedding, PendingCheckin, Role, ScanLog
from app.services import attendance_service as svc
from app.services import checkin_service, face_service, qr_service, session_service
from app.services.attendance_service import FaceCheckRefused
from app.services.face_engine import EMBEDDING_DIM, MODEL_NAME
from tests.face_helpers import FakeEngine, near, unit
from tests.factories import assign_faculty, enroll, make_course, make_record, make_user
from tests.liveness_helpers import live_person, offsets, photo_held_up, steps

T0 = datetime(2026, 11, 10, 4, 0, 0, tzinfo=timezone.utc)
MY_FACE = unit(5)


def register_face(student, vector=MY_FACE):
    db.session.add(FaceEmbedding(student=student, model_name=MODEL_NAME, dim=EMBEDDING_DIM, embedding=face_service.to_bytes(vector)))
    db.session.flush()


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
    challenge = PendingCheckin.query.one().challenge
    return faculty, session, student, challenge


def _complete(student, session, engine, now=T0 + timedelta(seconds=10)):
    return svc.complete_checkin(student, session.id, steps(), ip="10.0.0.9", now=now, engine=engine)


def _refused(call):
    with pytest.raises(FaceCheckRefused) as exc:
        call()
    return exc.value


def _face_logs():
    return [l.outcome for l in ScanLog.query.filter_by(event="face").order_by(ScanLog.id)]


# ---- success -----------------------------------------------------------------

def test_live_student_following_the_arrows_is_marked_present(checked_in):
    _, session, student, challenge = checked_in
    result = _complete(student, session, live_person(MY_FACE, challenge))
    assert result.status == "present"
    record = AttendanceRecord.query.one()
    assert (record.status, record.method, record.student_id) == ("present", "qr_face", student.id)
    assert 0 <= record.face_distance < 0.5
    assert PendingCheckin.query.count() == 0
    assert _face_logs() == ["matched"]
    detail = ScanLog.query.filter_by(event="face").one().detail
    assert detail.startswith(challenge) and "turn1" in detail and "turn2" in detail


def test_challenge_is_random_per_scan(checked_in):
    _, session, student, _ = checked_in
    seen = set()
    for i in range(30):
        moment = T0 + timedelta(seconds=i)
        checkin_service.begin_checkin(student, qr_service.make_token(session, moment)[0], now=moment)
        db.session.expire_all()
        seen.add(PendingCheckin.query.one().challenge)
    assert seen == {"LR", "RL"}


def test_second_tap_after_success_reports_already_present(checked_in):
    _, session, student, challenge = checked_in
    _complete(student, session, live_person(MY_FACE, challenge))
    again = _complete(student, session, live_person(MY_FACE, challenge))
    assert again.status == "already_present"
    assert AttendanceRecord.query.count() == 1


# ---- liveness attacks --------------------------------------------------------

def test_photo_or_phone_screen_of_the_student_is_rejected(checked_in):
    # The right face, but a flat picture cannot turn its head.
    _, session, student, challenge = checked_in
    problem = _refused(lambda: _complete(student, session, photo_held_up(MY_FACE, challenge)))
    assert (problem.reason, problem.attempts_left, problem.status_code) == ("liveness_failed", 2, 422)
    assert "turn your head" in problem.message
    assert AttendanceRecord.query.count() == 0
    assert PendingCheckin.query.one().face_attempts == 1   # costs an attempt: no endless retries
    assert _face_logs() == ["liveness_no_turn"]


def test_turning_in_the_wrong_order_is_rejected(checked_in):
    # A pre-recorded video turning the other way round.
    _, session, student, challenge = checked_in
    wrong = "RL" if challenge == "LR" else "LR"
    engine = FakeEngine(embeddings=[near(MY_FACE, seed=s) for s in (11, 12, 13)], offsets=offsets(wrong))
    assert _refused(lambda: _complete(student, session, engine)).reason == "liveness_failed"


def test_turning_only_one_way_is_rejected(checked_in):
    _, session, student, challenge = checked_in
    engine = live_person(MY_FACE, challenge, second=0.03)
    assert _refused(lambda: _complete(student, session, engine)).reason == "liveness_failed"


def test_photo_for_straight_then_own_head_for_turns_is_rejected(checked_in):
    # Straight frame: a photo of the absent student. Turns: the friend's own real head.
    _, session, student, challenge = checked_in
    friend = unit(99)
    engine = FakeEngine(embeddings=[near(MY_FACE, seed=1), near(friend, seed=2), near(friend, seed=3)], offsets=offsets(challenge))
    problem = _refused(lambda: _complete(student, session, engine))
    assert problem.reason == "liveness_failed" and "face changed" in problem.message
    assert _face_logs() == ["liveness_different_person"]


def test_someone_elses_live_face_does_not_match(checked_in):
    _, session, student, challenge = checked_in
    problem = _refused(lambda: _complete(student, session, live_person(unit(77), challenge)))
    assert (problem.reason, problem.attempts_left) == ("face_mismatch", 2)
    assert _face_logs() == ["face_mismatch"]


def test_three_failures_then_blocked_even_after_rescanning(checked_in):
    _, session, student, challenge = checked_in
    _refused(lambda: _complete(student, session, photo_held_up(MY_FACE, challenge)))
    _refused(lambda: _complete(student, session, live_person(unit(77), challenge)))
    third = _refused(lambda: _complete(student, session, photo_held_up(MY_FACE, challenge)))
    assert (third.reason, third.attempts_left, third.status_code) == ("too_many_attempts", 0, 403)

    later = T0 + timedelta(seconds=20)
    checkin_service.begin_checkin(student, qr_service.make_token(session, later)[0], now=later)
    db.session.expire_all()
    new_challenge = PendingCheckin.query.one().challenge
    blocked = _refused(lambda: _complete(student, session, live_person(MY_FACE, new_challenge), now=later + timedelta(seconds=1)))
    assert blocked.reason == "too_many_attempts"
    assert AttendanceRecord.query.count() == 0


def test_failure_then_proper_attempt_succeeds(checked_in):
    _, session, student, challenge = checked_in
    _refused(lambda: _complete(student, session, photo_held_up(MY_FACE, challenge)))
    assert _complete(student, session, live_person(MY_FACE, challenge)).status == "present"


# ---- problems that do not use an attempt ---------------------------------------

def test_bad_photo_does_not_use_an_attempt(checked_in):
    _, session, student, _ = checked_in
    with pytest.raises(face_service.FaceProblem) as exc:
        _complete(student, session, FakeEngine(detections=[]))
    assert exc.value.reason == "no_face"
    assert PendingCheckin.query.one().face_attempts == 0
    assert _face_logs() == ["photo_no_face"]


def test_naturally_slightly_turned_student_passes_both_ways(checked_in):
    # Real "straight" faces measured about -0.07. Turns are measured from that pose,
    # so turning to either side by the same amount works.
    _, session, student, challenge = checked_in
    engine = live_person(MY_FACE, challenge, straight=-0.07, first=0.13, second=0.13)
    assert _complete(student, session, engine).status == "present"


def test_photo_of_slightly_turned_face_still_fails(checked_in):
    _, session, student, challenge = checked_in
    problem = _refused(lambda: _complete(student, session, photo_held_up(MY_FACE, challenge, straight=0.10)))
    assert problem.reason == "liveness_failed"


def test_not_looking_straight_at_the_start(checked_in):
    _, session, student, challenge = checked_in
    engine = FakeEngine(embeddings=[near(MY_FACE)], offsets=[0.25, 0.25] + offsets(challenge)[1:])
    with pytest.raises(face_service.FaceProblem) as exc:
        _complete(student, session, engine)
    assert exc.value.reason == "not_straight" and "Look straight" in exc.value.message
    assert PendingCheckin.query.one().face_attempts == 0


def test_second_straight_frame_used_if_first_is_off(checked_in):
    _, session, student, challenge = checked_in
    engine = FakeEngine(embeddings=[near(MY_FACE, seed=s) for s in (11, 12, 13)], offsets=[0.3, 0.0] + offsets(challenge)[1:])
    assert _complete(student, session, engine).status == "present"



def test_unusable_frames_mid_turn_are_skipped(checked_in):
    # Some turn frames blurred or without a face: the remaining good frame still counts.
    _, session, student, challenge = checked_in
    engine = live_person(MY_FACE, challenge)
    original = engine.detect
    calls = {"n": 0}

    def flaky(image):
        calls["n"] += 1
        if calls["n"] in (2, 3, 6, 7):
            engine.offsets.pop(0)  # this frame is lost (blurred / no face found)
            return []
        return original(image)
    engine.detect = flaky
    assert _complete(student, session, engine).status == "present"


# ---- timing and state (unchanged rules) ----------------------------------------

def test_check_after_60_seconds_is_refused(checked_in):
    _, session, student, challenge = checked_in
    problem = _refused(lambda: _complete(student, session, live_person(MY_FACE, challenge), now=T0 + timedelta(seconds=61)))
    assert problem.reason == "checkin_expired"


def test_attendance_stopped_during_check(checked_in):
    faculty, session, student, challenge = checked_in
    session_service.stop_session(faculty, session.id, now=T0 + timedelta(seconds=5))
    problem = _refused(lambda: _complete(student, session, live_person(MY_FACE, challenge)))
    assert (problem.reason, problem.status_code) == ("session_closed", 410)
    assert AttendanceRecord.query.filter_by(student_id=student.id).one().status == "absent"


def test_no_scan_first(checked_in):
    _, session, student, challenge = checked_in
    PendingCheckin.query.delete()
    db.session.flush()
    assert _refused(lambda: _complete(student, session, live_person(MY_FACE, challenge))).reason == "no_checkin"


@pytest.mark.parametrize("bad_id", [0, 99999, None, True])
def test_unknown_session(checked_in, bad_id):
    _, _, student, challenge = checked_in
    with pytest.raises(FaceCheckRefused) as exc:
        svc.complete_checkin(student, bad_id, steps(), now=T0, engine=live_person(MY_FACE, challenge))
    assert exc.value.reason == "no_checkin"


def test_manual_record_blocks_face_check(checked_in):
    faculty, session, student, challenge = checked_in
    make_record(session=session, student=student, status="absent", method="manual", marked_by=faculty)
    assert _refused(lambda: _complete(student, session, live_person(MY_FACE, challenge))).reason == "already_recorded"


def test_face_registered_with_another_model_is_refused(checked_in):
    _, session, student, challenge = checked_in
    student.face.model_name = "old-model"
    db.session.flush()
    with pytest.raises(face_service.FaceNotRegistered):
        _complete(student, session, live_person(MY_FACE, challenge))


def test_left_right_can_be_flipped_by_config(checked_in, app, monkeypatch):
    # If real phones show the opposite sign, FACE_YAW_SIGN = -1 fixes it without code changes.
    _, session, student, challenge = checked_in
    monkeypatch.setitem(app.config, "FACE_YAW_SIGN", -1)
    mirrored = FakeEngine(embeddings=[near(MY_FACE, seed=s) for s in (11, 12, 13)],
                          offsets=[-o for o in offsets(challenge)])
    assert _complete(student, session, mirrored).status == "present"
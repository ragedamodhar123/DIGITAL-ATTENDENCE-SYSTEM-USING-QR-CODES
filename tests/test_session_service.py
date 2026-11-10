"""Module 4, part 1: starting, stopping and auto-closing attendance sessions."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models import AttendanceRecord, AttendanceSession, Enrollment, PendingCheckin, Role
from app.services import session_service as svc
from app.services.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from tests.factories import assign_faculty, enroll, make_course, make_record, make_user

T0 = datetime(2026, 11, 10, 4, 0, tzinfo=timezone.utc)  # 09:30 in India


@pytest.fixture
def course_setup():
    """DS301 taught by fac001, with three enrolled students."""
    faculty = make_user(role=Role.FACULTY, username="fac001")
    course = make_course(code="DS301")
    assign_faculty(course, faculty)
    students = [make_user(role=Role.STUDENT, username=f"stu{i:03d}") for i in range(3)]
    for student in students:
        enroll(course, student)
    return faculty, course, students


# ---- start -----------------------------------------------------------------

def test_start_creates_open_live_session(course_setup):
    faculty, course, _ = course_setup
    session, created = svc.start_session(faculty, course.id, now=T0)
    assert created is True
    assert (session.kind, session.status) == ("live", "open")
    assert session.started_at == T0
    assert session.expires_at == T0 + timedelta(minutes=120)
    assert len(session.qr_secret) == 64 and int(session.qr_secret, 16) >= 0
    assert session.session_date == date(2026, 11, 10)


def test_session_date_uses_college_time_not_utc(course_setup):
    faculty, course, _ = course_setup
    late_evening_utc = datetime(2026, 11, 10, 19, 30, tzinfo=timezone.utc)  # 01:00 on 11 Nov in India
    session, _ = svc.start_session(faculty, course.id, now=late_evening_utc)
    assert session.session_date == date(2026, 11, 11)


def test_every_session_gets_its_own_secret(course_setup):
    faculty, course, _ = course_setup
    first, _ = svc.start_session(faculty, course.id, now=T0)
    svc.stop_session(faculty, first.id, now=T0 + timedelta(minutes=50))
    second, _ = svc.start_session(faculty, course.id, now=T0 + timedelta(hours=3))
    assert first.qr_secret != second.qr_secret


def test_double_start_returns_the_same_session(course_setup):
    faculty, course, _ = course_setup
    first, created_first = svc.start_session(faculty, course.id, now=T0)
    again, created_again = svc.start_session(faculty, course.id, now=T0 + timedelta(seconds=2))
    assert (created_first, created_again) == (True, False)
    assert again.id == first.id
    assert AttendanceSession.query.count() == 1


def test_co_teacher_gets_the_same_open_session(course_setup):
    faculty, course, _ = course_setup
    co_teacher = make_user(role=Role.FACULTY, username="fac002")
    assign_faculty(course, co_teacher)
    first, _ = svc.start_session(faculty, course.id, now=T0)
    theirs, created = svc.start_session(co_teacher, course.id, now=T0)
    assert theirs.id == first.id and created is False


def test_simultaneous_start_race_keeps_one_session(course_setup, monkeypatch):
    # Two clicks landing at the same instant: the second request checked before the
    # first one saved, so it did not see the open session. Faking that check to say
    # "nothing open" leaves only the database's unique rule to stop a duplicate.
    faculty, course, _ = course_setup
    co_teacher = make_user(role=Role.FACULTY, username="fac002")
    assign_faculty(course, co_teacher)
    first, _ = svc.start_session(faculty, course.id, now=T0)

    monkeypatch.setattr(svc, "open_session_for_course", lambda course_id, now=None: None)
    session, created = svc.start_session(co_teacher, course.id, now=T0)

    assert session.id == first.id and created is False
    assert AttendanceSession.query.count() == 1


@pytest.mark.parametrize("who", ["not_assigned", "student", "admin", "inactive_faculty", "nobody"])
def test_only_assigned_active_faculty_can_start(course_setup, who):
    _, course, students = course_setup
    actor = {
        "not_assigned": lambda: make_user(role=Role.FACULTY),
        "student": lambda: students[0],
        "admin": lambda: make_user(role=Role.ADMIN),
        "inactive_faculty": lambda: _assigned_inactive(course),
        "nobody": lambda: None,
    }[who]()
    with pytest.raises(PermissionDenied):
        svc.start_session(actor, course.id, now=T0)
    assert AttendanceSession.query.count() == 0


def _assigned_inactive(course):
    faculty = make_user(role=Role.FACULTY, is_active=False)
    assign_faculty(course, faculty)
    return faculty


def test_unknown_or_retired_course(course_setup):
    faculty, course, _ = course_setup
    with pytest.raises(NotFound):
        svc.start_session(faculty, 99999, now=T0)
    course.is_active = False
    with pytest.raises(ValidationFailed):
        svc.start_session(faculty, course.id, now=T0)


def test_course_without_active_students_cannot_start():
    faculty = make_user(role=Role.FACULTY)
    course = make_course()
    assign_faculty(course, faculty)
    dropped = make_user(role=Role.STUDENT)
    db.session.add(Enrollment(course=course, student=dropped, is_active=False))
    enroll(course, make_user(role=Role.STUDENT, is_active=False))
    db.session.flush()
    with pytest.raises(ValidationFailed) as exc:
        svc.start_session(faculty, course.id, now=T0)
    assert "enrol" in exc.value.details["_body"][0]


def test_one_running_session_per_faculty(course_setup):
    faculty, course, _ = course_setup
    other = make_course(code="DB302")
    assign_faculty(other, faculty)
    enroll(other, make_user(role=Role.STUDENT))
    first, _ = svc.start_session(faculty, course.id, now=T0)
    with pytest.raises(Conflict) as exc:
        svc.start_session(faculty, other.id, now=T0)
    assert "DS301" in exc.value.details["_body"][0]
    svc.stop_session(faculty, first.id, now=T0 + timedelta(minutes=5))
    svc.start_session(faculty, other.id, now=T0 + timedelta(minutes=6))


# ---- stop ------------------------------------------------------------------

def test_stop_marks_everyone_without_a_record_absent(course_setup):
    faculty, course, students = course_setup
    session, _ = svc.start_session(faculty, course.id, now=T0)
    make_record(session=session, student=students[0], status="present", method="qr_face")
    stopped = svc.stop_session(faculty, session.id, now=T0 + timedelta(minutes=50))

    assert stopped.status == "closed" and stopped.ended_at == T0 + timedelta(minutes=50)
    records = {r.student.username: (r.status, r.method) for r in AttendanceRecord.query.filter_by(session_id=session.id)}
    assert records == {
        "stu000": ("present", "qr_face"),
        "stu001": ("absent", "auto_absent"),
        "stu002": ("absent", "auto_absent"),
    }


def test_dropped_and_deactivated_students_are_not_marked_absent(course_setup):
    faculty, course, students = course_setup
    session, _ = svc.start_session(faculty, course.id, now=T0)
    Enrollment.query.filter_by(student_id=students[1].id).one().is_active = False
    students[2].is_active = False
    db.session.flush()
    svc.stop_session(faculty, session.id, now=T0 + timedelta(minutes=50))
    assert [r.student.username for r in AttendanceRecord.query.filter_by(session_id=session.id)] == ["stu000"]


def test_present_student_who_was_dropped_mid_class_stays_present(course_setup):
    faculty, course, students = course_setup
    session, _ = svc.start_session(faculty, course.id, now=T0)
    make_record(session=session, student=students[0], status="present", method="qr_face")
    Enrollment.query.filter_by(student_id=students[0].id).one().is_active = False
    svc.stop_session(faculty, session.id, now=T0 + timedelta(minutes=50))
    record = AttendanceRecord.query.filter_by(session_id=session.id, student_id=students[0].id).one()
    assert record.status == "present"


def test_stop_removes_unfinished_check_ins(course_setup):
    faculty, course, students = course_setup
    session, _ = svc.start_session(faculty, course.id, now=T0)
    db.session.add(PendingCheckin(session=session, student=students[0], created_at=T0, expires_at=T0 + timedelta(seconds=60)))
    db.session.flush()
    svc.stop_session(faculty, session.id, now=T0 + timedelta(seconds=30))
    assert PendingCheckin.query.count() == 0


def test_stop_twice_is_harmless(course_setup):
    faculty, course, _ = course_setup
    session, _ = svc.start_session(faculty, course.id, now=T0)
    svc.stop_session(faculty, session.id, now=T0 + timedelta(minutes=50))
    again = svc.stop_session(faculty, session.id, now=T0 + timedelta(minutes=51))
    assert again.ended_at == T0 + timedelta(minutes=50)
    assert AttendanceRecord.query.filter_by(session_id=session.id).count() == 3


def test_co_teacher_can_stop_but_others_cannot(course_setup):
    faculty, course, _ = course_setup
    co_teacher, outsider = make_user(role=Role.FACULTY), make_user(role=Role.FACULTY)
    assign_faculty(course, co_teacher)
    session, _ = svc.start_session(faculty, course.id, now=T0)
    with pytest.raises(PermissionDenied):
        svc.stop_session(outsider, session.id, now=T0 + timedelta(minutes=1))
    assert db.session.get(AttendanceSession, session.id).status == "open"
    svc.stop_session(co_teacher, session.id, now=T0 + timedelta(minutes=2))


@pytest.mark.parametrize("bad_id", [0, 99999, None, True, "1"])
def test_stop_unknown_session(course_setup, bad_id):
    faculty, _, _ = course_setup
    with pytest.raises(NotFound):
        svc.stop_session(faculty, bad_id, now=T0)


# ---- automatic closing -----------------------------------------------------

def test_forgotten_session_closes_itself_at_its_expiry_time(course_setup):
    faculty, course, _ = course_setup
    session, _ = svc.start_session(faculty, course.id, now=T0)
    later = T0 + timedelta(hours=5)
    assert svc.open_session_for_course(course.id, now=later) is None
    db.session.refresh(session)
    assert session.status == "closed"
    assert session.ended_at == T0 + timedelta(minutes=120)  # when it really stopped, not "now"
    assert AttendanceRecord.query.filter_by(session_id=session.id, status="absent").count() == 3


def test_not_closed_before_its_time(course_setup):
    faculty, course, _ = course_setup
    session, _ = svc.start_session(faculty, course.id, now=T0)
    assert svc.close_expired_sessions(now=T0 + timedelta(minutes=119)) == 0
    assert svc.open_session_for_course(course.id, now=T0 + timedelta(minutes=119)).id == session.id


def test_stopping_an_expired_session_records_expiry_as_end(course_setup):
    faculty, course, _ = course_setup
    session, _ = svc.start_session(faculty, course.id, now=T0)
    svc.stop_session(faculty, session.id, now=T0 + timedelta(hours=4))
    assert session.ended_at == session.expires_at


def test_new_session_after_auto_close(course_setup):
    faculty, course, _ = course_setup
    first, _ = svc.start_session(faculty, course.id, now=T0)
    second, created = svc.start_session(faculty, course.id, now=T0 + timedelta(hours=3))
    assert created is True and second.id != first.id


def test_two_lectures_same_day_are_separate_sessions(course_setup):
    faculty, course, _ = course_setup
    morning, _ = svc.start_session(faculty, course.id, now=T0)
    svc.stop_session(faculty, morning.id, now=T0 + timedelta(minutes=50))
    afternoon, _ = svc.start_session(faculty, course.id, now=T0 + timedelta(hours=4))
    assert morning.session_date == afternoon.session_date and morning.id != afternoon.id


def test_cli_closes_expired_sessions(cli, course_setup):
    faculty, course, _ = course_setup
    session = svc.start_session(faculty, course.id, now=T0 - timedelta(hours=5))[0]
    db.session.commit()
    result = cli.invoke(args=["close-expired-sessions"])
    assert result.exit_code == 0 and "Closed 1" in result.output
    db.session.expire_all()
    assert db.session.get(AttendanceSession, session.id).status == "closed"


# ---- reading ---------------------------------------------------------------

def test_summary_while_open_and_after_closing(course_setup):
    faculty, course, students = course_setup
    session, _ = svc.start_session(faculty, course.id, now=T0)
    make_record(session=session, student=students[0], status="present", method="qr_face")
    assert svc.summary(session) == {"expected": 3, "present": 1, "not_yet": 2, "absent": 0}

    svc.stop_session(faculty, session.id, now=T0 + timedelta(minutes=50))
    enroll(course, make_user(role=Role.STUDENT))  # joins later: not part of this session
    assert svc.summary(session) == {"expected": 3, "present": 1, "not_yet": 0, "absent": 2}


def test_only_teaching_faculty_can_view_a_session(course_setup):
    faculty, course, _ = course_setup
    session, _ = svc.start_session(faculty, course.id, now=T0)
    assert svc.get_session_for_faculty(faculty, session.id, now=T0).id == session.id
    with pytest.raises(PermissionDenied):
        svc.get_session_for_faculty(make_user(role=Role.FACULTY), session.id, now=T0)
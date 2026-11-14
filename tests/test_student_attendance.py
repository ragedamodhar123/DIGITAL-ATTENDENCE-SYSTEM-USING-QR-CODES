"""Module 6, part 1: a student's own attendance view."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models import Enrollment, Role
from app.services import manual_service, session_service
from app.services import student_attendance_service as svc
from app.services.errors import NotFound
from tests.factories import TEST_PASSWORD, assign_faculty, enroll, make_course, make_record, make_user

T0 = datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc)


@pytest.fixture
def term():
    """DS301 with 4 finished classes: stu001 present in 3 (one by faculty), absent in 1."""
    faculty = make_user(role=Role.FACULTY, username="fac001")
    course = make_course(code="DS301", name="Data Structures")
    assign_faculty(course, faculty)
    me = make_user(role=Role.STUDENT, username="stu001", must_change_password=False)
    classmate = make_user(role=Role.STUDENT, username="stu002")
    enroll(course, me)
    enroll(course, classmate)
    sessions = []
    for day in range(4):
        start = T0 + timedelta(days=day)
        session, _ = session_service.start_session(faculty, course.id, now=start)
        if day in (0, 1):
            make_record(session=session, student=me, status="present", method="qr_face")
        make_record(session=session, student=classmate, status="present", method="qr_face")
        session_service.stop_session(faculty, session.id, now=start + timedelta(minutes=50))
        sessions.append(session)
    manual_service.set_status(faculty, sessions[2].id, "stu001", "present", "Was in class, phone died")
    return faculty, course, me, classmate, sessions


def test_course_totals_and_percentage(term):
    _, course, me, _, _ = term
    (summary,) = svc.course_summaries(me)
    assert (summary.course, summary.present, summary.absent, summary.total) == (course, 3, 1, 4)
    assert summary.percent == 75.0 and summary.below_threshold is False and summary.enrolled is True


def test_below_threshold_flag(term, app, monkeypatch):
    _, _, me, _, _ = term
    monkeypatch.setitem(app.config, "ATTENDANCE_WARNING_PERCENT", 80)
    assert svc.course_summaries(me)[0].below_threshold is True


def test_running_class_does_not_count_yet(term):
    faculty, course, me, _, _ = term
    session_service.start_session(faculty, course.id)
    (summary,) = svc.course_summaries(me)
    assert summary.total == 4 and summary.open_session is not None


def test_late_joiner_counted_only_from_joining(term):
    faculty, course, _, _, _ = term
    newcomer = make_user(role=Role.STUDENT, username="new001")
    enroll(course, newcomer)
    session, _ = session_service.start_session(faculty, course.id, now=T0 + timedelta(days=10))
    session_service.stop_session(faculty, session.id, now=T0 + timedelta(days=10, minutes=50))
    (summary,) = svc.course_summaries(newcomer)
    assert (summary.present, summary.absent, summary.percent) == (0, 1, 0.0)


def test_course_without_classes_yet(term):
    _, _, me, _, _ = term
    other = make_course(code="DB302")
    enroll(other, me)
    summaries = {s.course.code: s for s in svc.course_summaries(me)}
    assert summaries["DB302"].percent is None and summaries["DB302"].total == 0


def test_dropped_course_keeps_its_history(term):
    _, course, me, _, _ = term
    Enrollment.query.filter_by(student_id=me.id).one().is_active = False
    (summary,) = svc.course_summaries(me)
    assert summary.enrolled is False and summary.total == 4


def test_retired_course_without_history_is_hidden(term):
    _, _, me, _, _ = term
    retired = make_course(code="OLD101")
    retired.is_active = False
    enroll(retired, me)
    assert [s.course.code for s in svc.course_summaries(me)] == ["DS301"]


def test_manual_session_for_another_date_counts(term):
    faculty, course, me, _, _ = term
    manual_service.create_manual_session(faculty, course.id, date(2026, 10, 30), ["stu001"], "Roll on paper", now=T0)
    assert svc.course_summaries(me)[0].total == 5


def test_class_by_class_history(term):
    _, course, me, _, sessions = term
    summary, history = svc.course_detail(me, course.id)
    assert [s.id for s, _ in history] == [s.id for s in reversed(sessions)]  # newest first
    methods = [(r.status, r.method) for _, r in history]
    assert methods == [("absent", "auto_absent"), ("present", "manual"), ("present", "qr_face"), ("present", "qr_face")]


def test_history_includes_running_class_not_checked_in(term):
    faculty, course, me, _, _ = term
    live, _ = session_service.start_session(faculty, course.id)
    _, history = svc.course_detail(me, course.id)
    assert history[0] == (live, None)


def test_other_courses_are_not_visible(term):
    _, _, me, _, _ = term
    strangers_course = make_course(code="SECRET1")
    for bad in (strangers_course.id, 99999, 0, None, True):
        with pytest.raises(NotFound):
            svc.course_detail(me, bad)


# ---- pages ---------------------------------------------------------------------

def _login(client, username="stu001"):
    db.session.commit()
    client.post("/login", data={"username": username, "password": TEST_PASSWORD})


def _give_face(student):
    import struct

    from app.models import FaceEmbedding

    db.session.add(FaceEmbedding(student=student, model_name="t", dim=128, embedding=struct.pack("<128f", *[0.0] * 128)))


def test_home_page_shows_percentages(client, term):
    _, course, me, _, _ = term
    _give_face(me)
    _login(client)
    page = client.get("/student/").data.decode()
    assert "75.0%" in page and "3 of 4" in page and f"/student/courses/{course.id}" in page


def test_course_page_shows_each_class_without_faculty_reasons(client, term):
    _, course, me, _, _ = term
    _give_face(me)
    _login(client)
    page = client.get(f"/student/courses/{course.id}").data.decode()
    assert page.count('<span class="status status-ok">Present</span>') == 3
    assert "did not check in" in page and "marked by faculty" in page and "face check" in page
    assert "phone died" not in page  # the faculty's reason is not shown to students


def test_low_attendance_warning_on_page(client, term, web_app):
    _, course, me, _, _ = term
    _give_face(me)
    _login(client)
    web_app.config["ATTENDANCE_WARNING_PERCENT"] = 90
    try:
        page = client.get(f"/student/courses/{course.id}").data.decode()
    finally:
        web_app.config["ATTENDANCE_WARNING_PERCENT"] = 75
    assert "Your attendance is below 90%" in page


def test_student_cannot_open_someone_elses_course_or_faculty_pages(client, term):
    _, _, me, _, sessions = term
    _give_face(me)
    other = make_course(code="SECRET1")
    _login(client)
    assert client.get(f"/student/courses/{other.id}").status_code == 404
    assert client.get(f"/faculty/sessions/{sessions[0].id}").status_code == 403
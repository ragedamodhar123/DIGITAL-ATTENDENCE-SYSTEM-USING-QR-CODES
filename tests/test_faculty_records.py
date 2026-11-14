"""Module 5, part 2: faculty results, corrections, history and other dates (pages)."""
import re
from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import AttendanceAudit, AttendanceRecord, AttendanceSession, Role
from app.services import session_service
from app.utils.timeutils import local_date
from tests.factories import TEST_PASSWORD, assign_faculty, enroll, make_course, make_record, make_user


@pytest.fixture
def klass():
    faculty = make_user(role=Role.FACULTY, username="fac001", must_change_password=False, full_name="Dr Rao")
    course = make_course(code="DS301")
    assign_faculty(course, faculty)
    students = [make_user(role=Role.STUDENT, username=f"stu{i:03d}", full_name=f"Student {i}") for i in range(3)]
    for s in students:
        enroll(course, s)
    db.session.commit()
    return faculty, course, students


@pytest.fixture
def finished(klass):
    """A finished class: stu000 present by face check, the others absent."""
    faculty, course, students = klass
    session, _ = session_service.start_session(faculty, course.id)
    make_record(session=session, student=students[0], status="present", method="qr_face")
    db.session.commit()
    session_service.stop_session(faculty, session.id)
    return faculty, course, students, session


def _login(client, username="fac001"):
    client.post("/login", data={"username": username, "password": TEST_PASSWORD})


def _status(session, student):
    db.session.expire_all()
    return AttendanceRecord.query.filter_by(session_id=session.id, student_id=student.id).one().status


# ---- course page -----------------------------------------------------------------

def test_course_page_lists_sessions_with_counts(client, finished):
    _, course, _, session = finished
    _login(client)
    page = client.get(f"/faculty/courses/{course.id}").data.decode()
    assert f"/faculty/sessions/{session.id}" in page
    assert "1 present, 2 absent" in page
    assert "Record another date" in page


def test_course_page_without_sessions(client, klass):
    _, course, _ = klass
    _login(client)
    assert "No attendance taken yet." in client.get(f"/faculty/courses/{course.id}").data.decode()


# ---- session page ----------------------------------------------------------------

def test_finished_session_shows_every_student_and_how_they_were_marked(client, finished):
    _, _, _, session = finished
    _login(client)
    page = client.get(f"/faculty/sessions/{session.id}").data.decode()
    assert page.count('class="roster-row"') == 3
    assert "face check" in page and "did not check in" in page
    assert "Change selected students" in page


def test_live_session_page_lists_not_yet_checked_in(client, klass):
    faculty, course, _ = klass
    session, _ = session_service.start_session(faculty, course.id)
    _login(client)
    page = client.get(f"/faculty/sessions/{session.id}").data.decode()
    assert page.count("Not checked in yet") == 3 and 'id="live-session"' in page


# ---- corrections ---------------------------------------------------------------------

def test_change_one_student_with_reason(client, finished):
    faculty, _, students, session = finished
    _login(client)
    resp = client.post(f"/faculty/sessions/{session.id}/marks",
                       data={"students": "stu001", "status": "present", "reason": "Phone battery was dead"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/faculty/sessions/{session.id}#roster")
    assert _status(session, students[1]) == "present"
    page = client.get(f"/faculty/sessions/{session.id}").data.decode()
    assert "Changed: STU001." in page
    assert "marked by Dr Rao" in page and "changed 1 time" in page


def test_change_several_students_at_once(client, finished):
    _, _, students, session = finished
    _login(client)
    client.post(f"/faculty/sessions/{session.id}/marks",
                data={"students": ["stu000", "stu001", "stu002"], "status": "present", "reason": "Lab moved, all attended"})
    assert [_status(session, s) for s in students] == ["present", "present", "present"]
    page = client.get(f"/faculty/sessions/{session.id}").data.decode()
    assert "Changed: STU001, STU002." in page and "Already that status: STU000." in page
    assert AttendanceAudit.query.count() == 2


def test_missing_reason_changes_nothing(client, finished):
    _, _, students, session = finished
    _login(client)
    resp = client.post(f"/faculty/sessions/{session.id}/marks", data={"students": "stu001", "status": "present", "reason": " "})
    assert "Say why attendance is being changed" in client.get(resp.headers["Location"]).data.decode()
    assert _status(session, students[1]) == "absent"


def test_no_student_ticked(client, finished):
    _, _, _, session = finished
    _login(client)
    resp = client.post(f"/faculty/sessions/{session.id}/marks", data={"status": "present", "reason": "Something"})
    assert "Choose at least one student." in client.get(resp.headers["Location"]).data.decode()


def test_unknown_student_reported(client, finished):
    _, _, _, session = finished
    _login(client)
    resp = client.post(f"/faculty/sessions/{session.id}/marks", data={"students": "ghost01", "status": "present", "reason": "Test reason"})
    assert "GHOST01: No student with this username." in client.get(resp.headers["Location"]).data.decode()


def test_mark_present_during_live_class(client, klass):
    faculty, course, students = klass
    session, _ = session_service.start_session(faculty, course.id)
    _login(client)
    client.post(f"/faculty/sessions/{session.id}/marks",
                data={"students": "stu002", "status": "present", "reason": "Face check failed 3 times"})
    assert _status(session, students[2]) == "present"


def test_outsiders_cannot_change_or_view(client, web_app, finished):
    _, _, students, session = finished
    make_user(role=Role.FACULTY, username="fac999", must_change_password=False)
    db.session.commit()
    _login(client, "fac999")
    resp = client.post(f"/faculty/sessions/{session.id}/marks", data={"students": "stu001", "status": "present", "reason": "Not mine"})
    assert resp.status_code == 403
    assert client.get(f"/faculty/sessions/{session.id}/students/stu001/history").status_code == 403
    assert _status(session, students[1]) == "absent"


def test_changes_need_post_and_csrf(client, web_app, finished):
    _, _, students, session = finished
    _login(client)
    assert client.get(f"/faculty/sessions/{session.id}/marks").status_code == 405
    web_app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post(f"/faculty/sessions/{session.id}/marks", data={"students": "stu001", "status": "present", "reason": "Csrf test"})
    finally:
        web_app.config["WTF_CSRF_ENABLED"] = False
    assert resp.status_code == 400 and _status(session, students[1]) == "absent"


# ---- history -------------------------------------------------------------------------

def test_history_page_shows_every_change(client, finished):
    _, _, _, session = finished
    _login(client)
    url = f"/faculty/sessions/{session.id}/marks"
    client.post(url, data={"students": "stu001", "status": "present", "reason": "First fix"})
    client.post(url, data={"students": "stu001", "status": "absent", "reason": "Checked again, left early"})
    page = client.get(f"/faculty/sessions/{session.id}/students/stu001/history").data.decode()
    assert page.index("First fix") < page.index("Checked again, left early")
    assert "absent &rarr; present" in page and "present &rarr; absent" in page and "by Dr Rao" in page


def test_history_of_unchanged_face_record(client, finished):
    _, _, _, session = finished
    _login(client)
    page = client.get(f"/faculty/sessions/{session.id}/students/stu000/history").data.decode()
    assert "No manual changes" in page and "face check" in page


# ---- another date ------------------------------------------------------------------------

def test_other_date_form(client, klass):
    _, course, _ = klass
    _login(client)
    page = client.get(f"/faculty/courses/{course.id}/attendance/other-date").data.decode()
    assert page.count('name="present"') == 3 and f'max="{local_date().isoformat()}"' in page and "select-all.js" in page


def test_save_attendance_for_a_past_date(client, klass):
    _, course, students = klass
    _login(client)
    past = (local_date() - timedelta(days=7)).isoformat()
    resp = client.post(f"/faculty/courses/{course.id}/attendance/other-date",
                       data={"session_date": past, "present": ["stu000", "stu002"], "reason": "Roll taken on paper"})
    assert resp.status_code == 302
    session = AttendanceSession.query.one()
    assert (session.kind, session.session_date.isoformat()) == ("manual", past)
    assert [_status(session, s) for s in students] == ["present", "absent", "present"]
    page = client.get(resp.headers["Location"]).data.decode()
    assert "2 present, 1 absent" in page and "recorded by hand" in page


def test_future_date_refused_and_form_keeps_input(client, klass):
    _, course, _ = klass
    _login(client)
    future = (local_date() + timedelta(days=1)).isoformat()
    resp = client.post(f"/faculty/courses/{course.id}/attendance/other-date",
                       data={"session_date": future, "present": ["stu001"], "reason": "Keep this reason"})
    page = resp.data.decode()
    assert resp.status_code == 422 and "future date" in page
    assert 'value="Keep this reason"' in page and re.search(r'value="stu001"\s+id="p-\d+"\s+checked', page)
    assert AttendanceSession.query.count() == 0


@pytest.mark.parametrize("bad", ["", "not-a-date", "2026-13-45"])
def test_invalid_date(client, klass, bad):
    _, course, _ = klass
    _login(client)
    resp = client.post(f"/faculty/courses/{course.id}/attendance/other-date", data={"session_date": bad, "reason": "Paper roll"})
    assert resp.status_code == 422 and "Choose a valid date" in resp.data.decode()


def test_same_day_needs_confirmation(client, finished):
    _, course, _, session = finished
    _login(client)
    url = f"/faculty/courses/{course.id}/attendance/other-date"
    day = session.session_date.isoformat()
    first = client.post(url, data={"session_date": day, "present": ["stu001"], "reason": "Second lecture today"})
    assert first.status_code == 409 and "another class on the same day" in first.data.decode()
    assert AttendanceSession.query.count() == 1
    confirmed = client.post(url, data={"session_date": day, "present": ["stu001"], "reason": "Second lecture today", "confirm_same_day": "yes"})
    assert confirmed.status_code == 302 and AttendanceSession.query.count() == 2


def test_missing_reason_on_other_date(client, klass):
    _, course, _ = klass
    _login(client)
    resp = client.post(f"/faculty/courses/{course.id}/attendance/other-date",
                       data={"session_date": (local_date() - timedelta(days=1)).isoformat(), "reason": ""})
    assert resp.status_code == 422 and "Say why attendance is being changed" in resp.data.decode()


def test_other_date_only_for_own_courses(client, klass):
    _, course, _ = klass
    make_user(role=Role.FACULTY, username="fac999", must_change_password=False)
    db.session.commit()
    _login(client, "fac999")
    assert client.get(f"/faculty/courses/{course.id}/attendance/other-date").status_code == 403


def test_select_all_script_is_served(client):
    assert b"data-select-all" in client.get("/static/js/select-all.js").data
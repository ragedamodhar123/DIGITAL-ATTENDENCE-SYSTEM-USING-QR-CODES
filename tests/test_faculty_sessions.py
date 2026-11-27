"""Module 4, part 2: faculty start/stop pages and the live QR endpoint."""
import re

import pytest

from app.extensions import db
from app.models import AttendanceRecord, AttendanceSession, Role
from app.services import qr_service
from tests.factories import TEST_PASSWORD, assign_faculty, enroll, make_course, make_record, make_user


@pytest.fixture
def setup():
    faculty = make_user(role=Role.FACULTY, username="fac001", must_change_password=False)
    course = make_course(code="DS301")
    assign_faculty(course, faculty)
    students = [make_user(role=Role.STUDENT, username=f"stu{i:03d}", full_name=f"Student {i}") for i in range(3)]
    for s in students:
        enroll(course, s)
    db.session.commit()
    return faculty, course, students


TUNNEL = "https://abc.trycloudflare.com"


def _login(client, username="fac001", base_url="http://localhost"):
    # Cookies belong to one site: log in on the same address the test then uses.
    client.post("/login", data={"username": username, "password": TEST_PASSWORD}, base_url=base_url)


def _start(client, course, base_url="http://localhost"):
    resp = client.post(f"/faculty/courses/{course.id}/attendance/start", base_url=base_url)
    assert resp.status_code == 302, resp.data
    return int(resp.headers["Location"].rsplit("/", 1)[1])


# ---- starting ----------------------------------------------------------------

def test_course_page_offers_start_then_resume(client, setup):
    _, course, _ = setup
    _login(client)
    assert b"Start attendance" in client.get(f"/faculty/courses/{course.id}").data
    session_id = _start(client, course)
    page = client.get(f"/faculty/courses/{course.id}").data.decode()
    assert "Open attendance screen" in page and f"/faculty/sessions/{session_id}" in page


def test_start_twice_lands_on_the_same_screen(client, setup):
    _, course, _ = setup
    _login(client)
    assert _start(client, course) == _start(client, course)
    assert AttendanceSession.query.count() == 1


def test_start_needs_post(client, setup):
    _, course, _ = setup
    _login(client)
    assert client.get(f"/faculty/courses/{course.id}/attendance/start").status_code == 405


def test_start_problem_is_shown_on_course_page(client, setup):
    faculty, course, _ = setup
    other = make_course(code="DB302")
    assign_faculty(other, faculty)
    enroll(other, make_user(role=Role.STUDENT))
    db.session.commit()
    _login(client)
    _start(client, course)
    resp = client.post(f"/faculty/courses/{other.id}/attendance/start")
    assert resp.headers["Location"] == f"/faculty/courses/{other.id}"
    assert b"Stop attendance for DS301 first" in client.get(resp.headers["Location"]).data


def test_other_faculty_cannot_start_or_watch(client, web_app, setup):
    _, course, _ = setup
    make_user(role=Role.FACULTY, username="fac999", must_change_password=False)
    db.session.commit()
    _login(client)
    session_id = _start(client, course)
    outsider = web_app.test_client()
    _login(outsider, "fac999")
    assert outsider.post(f"/faculty/courses/{course.id}/attendance/start").status_code == 403
    assert outsider.get(f"/faculty/sessions/{session_id}").status_code == 403
    assert outsider.get(f"/api/faculty/sessions/{session_id}/live").status_code == 403


# ---- live screen ---------------------------------------------------------------

def test_session_page_has_the_live_screen(client, setup):
    _, course, _ = setup
    _login(client)
    page = client.get(f"/faculty/sessions/{_start(client, course)}").data.decode()
    assert 'id="live-session"' in page and "faculty-session.js" in page and "Stop attendance" in page


def test_live_endpoint_returns_a_valid_scannable_code(client, setup):
    _, course, _ = setup
    _login(client, base_url=TUNNEL)
    session_id = _start(client, course, base_url=TUNNEL)
    body = client.get(f"/api/faculty/sessions/{session_id}/live", base_url=TUNNEL).get_json()

    assert body["status"] == "open"
    assert body["counts"] == {"expected": 3, "present": 0, "not_yet": 3, "absent": 0}
    assert 0 < body["qr"]["refresh_in_ms"] <= 10000 and body["qr"]["rotation_ms"] == 10000
    assert body["qr"]["svg"].startswith("<svg")
    assert re.match(r"^\d{2}:\d{2}$", body["ends_at"])


def test_live_counts_and_latest_check_ins(client, setup):
    _, course, students = setup
    _login(client)
    session_id = _start(client, course)
    session = db.session.get(AttendanceSession, session_id)
    make_record(session=session, student=students[1], status="present", method="qr_face")
    db.session.commit()
    body = client.get(f"/api/faculty/sessions/{session_id}/live").get_json()
    assert body["counts"]["present"] == 1 and body["counts"]["not_yet"] == 2
    assert body["recent"][0]["name"] == "Student 1" and body["recent"][0]["username"] == "STU001"


def test_link_in_qr_uses_the_address_the_faculty_opened(client, setup, monkeypatch):
    _, course, _ = setup
    _login(client, base_url=TUNNEL)
    session_id = _start(client, course, base_url=TUNNEL)
    seen = {}
    real = qr_service.scan_url
    monkeypatch.setattr(qr_service, "scan_url", lambda host, token: seen.setdefault("url", real(host, token)))
    client.get(f"/api/faculty/sessions/{session_id}/live", base_url=TUNNEL)
    token = seen["url"].rsplit("/a/", 1)[1]
    assert seen["url"].startswith("https://abc.trycloudflare.com/a/")
    assert qr_service.verify_token(token).id == session_id


def test_local_address_warning(client, setup):
    _, course, _ = setup
    _login(client)
    session_id = _start(client, course)
    assert b"phones cannot open" in client.get(f"/faculty/sessions/{session_id}").data
    tunnel_client = client.application.test_client()
    _login(tunnel_client, base_url=TUNNEL)
    page = tunnel_client.get(f"/faculty/sessions/{session_id}", base_url=TUNNEL).data
    assert b'id="live-session"' in page and b"phones cannot open" not in page


def test_live_endpoint_needs_login_as_json(client, setup):
    resp = client.get("/api/faculty/sessions/1/live")
    assert resp.status_code == 401 and resp.is_json


def test_student_cannot_see_the_code(client, setup):
    _, course, _ = setup
    _login(client)
    session_id = _start(client, course)
    student_client = client.application.test_client()
    make_user(role=Role.STUDENT, username="peek01", must_change_password=False)
    db.session.commit()
    _login(student_client, "peek01")
    assert student_client.get(f"/api/faculty/sessions/{session_id}/live").status_code == 403


# ---- stopping --------------------------------------------------------------------

def test_stop_shows_final_summary_and_live_reports_closed(client, setup):
    _, course, students = setup
    _login(client)
    session_id = _start(client, course)
    make_record(session=db.session.get(AttendanceSession, session_id), student=students[0], status="present", method="qr_face")
    db.session.commit()

    resp = client.post(f"/faculty/sessions/{session_id}/stop")
    assert resp.headers["Location"] == f"/faculty/sessions/{session_id}"
    page = client.get(resp.headers["Location"]).data.decode()
    assert "attendance closed at" in page and "faculty-session.js" not in page
    assert "Students not marked present are recorded as absent" in page

    body = client.get(f"/api/faculty/sessions/{session_id}/live").get_json()
    assert body["status"] == "closed" and "qr" not in body
    assert body["counts"] == {"expected": 3, "present": 1, "not_yet": 0, "absent": 2}
    assert AttendanceRecord.query.filter_by(session_id=session_id).count() == 3


def test_stop_needs_post_and_csrf(client, web_app, setup):
    _, course, _ = setup
    _login(client)
    session_id = _start(client, course)
    assert client.get(f"/faculty/sessions/{session_id}/stop").status_code == 405
    web_app.config["WTF_CSRF_ENABLED"] = True
    try:
        assert client.post(f"/faculty/sessions/{session_id}/stop").status_code == 400
    finally:
        web_app.config["WTF_CSRF_ENABLED"] = False
    assert db.session.get(AttendanceSession, session_id).status == "open"


def test_unknown_session_is_404(client, setup):
    _login(client)
    assert client.get("/faculty/sessions/99999").status_code == 404
    assert client.get("/api/faculty/sessions/99999/live").status_code == 404


def test_live_screen_script_is_served(client):
    body = client.get("/static/js/faculty-session.js").data
    assert b"codeValidUntil" in body and b"wakeLock" in body
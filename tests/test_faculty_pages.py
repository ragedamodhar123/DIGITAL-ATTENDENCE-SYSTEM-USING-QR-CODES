"""Module 2, part 3: faculty course roster page and issuing student passwords."""
import re
import struct

import pytest

from app.extensions import db
from app.models import Enrollment, FaceEmbedding, Role
from tests.factories import TEST_PASSWORD, assign_faculty, enroll, make_course, make_user

TEMP = re.compile(rb'class="temp-password"[^>]*>([a-z0-9-]+)<')


@pytest.fixture
def setup():
    faculty = make_user(role=Role.FACULTY, username="fac001", must_change_password=False)
    course = make_course(code="DS301")
    assign_faculty(course, faculty)
    ready = make_user(role=Role.STUDENT, username="stu001", must_change_password=False, full_name="Ready One")
    db.session.add(FaceEmbedding(student=ready, model_name="t", dim=128, embedding=struct.pack("<128f", *[0.0] * 128)))
    new = make_user(role=Role.STUDENT, username="stu002", full_name="New Two")
    gone = make_user(role=Role.STUDENT, username="stu003", is_active=False, full_name="Gone Three")
    for s in (ready, new, gone):
        enroll(course, s)
    db.session.commit()
    return faculty, course, ready


def _login(client, username="fac001"):
    client.post("/login", data={"username": username, "password": TEST_PASSWORD})


def test_dashboard_links_to_course_page(client, setup):
    _, course, _ = setup
    _login(client)
    assert f"/faculty/courses/{course.id}".encode() in client.get("/faculty/").data


def test_roster_shows_each_students_status(client, setup):
    _, course, _ = setup
    _login(client)
    page = client.get(f"/faculty/courses/{course.id}").data.decode()
    assert "Ready One" in page and "Ready" in page
    assert "Has not set a password yet" in page
    assert "Account deactivated" in page
    assert page.count("New password") == 2  # no button for the deactivated student


def test_issue_password_shows_it_once_and_it_works(client, web_app, setup):
    _, course, _ = setup
    _login(client)
    resp = client.post(f"/faculty/courses/{course.id}/students/stu001/temporary-password")
    assert resp.status_code == 200
    assert "no-store" in resp.headers["Cache-Control"]
    temp = TEMP.search(resp.data).group(1).decode()

    other = web_app.test_client()
    login = other.post("/login", data={"username": "stu001", "password": temp})
    assert login.headers["Location"] == "/change-password"
    # Not stored in the faculty member's session cookie either.
    assert temp.encode() not in (client.get_cookie("session").value.encode())


def test_cannot_issue_for_student_outside_this_course(client, setup):
    _, course, _ = setup
    make_user(role=Role.STUDENT, username="stranger")
    other_course = make_course(code="DB302")
    db.session.commit()
    _login(client)
    assert client.post(f"/faculty/courses/{course.id}/students/stranger/temporary-password").status_code == 404
    assert client.post(f"/faculty/courses/{course.id}/students/bad%20name/temporary-password").status_code == 404
    assert client.get(f"/faculty/courses/{other_course.id}").status_code == 403


def test_dropped_student_cannot_be_reset_from_course_page(client, setup):
    _, course, ready = setup
    Enrollment.query.filter_by(student_id=ready.id).one().is_active = False
    db.session.commit()
    _login(client)
    assert client.post(f"/faculty/courses/{course.id}/students/stu001/temporary-password").status_code == 404


def test_reset_requires_post_and_csrf(client, web_app, setup):
    _, course, _ = setup
    _login(client)
    assert client.get(f"/faculty/courses/{course.id}/students/stu001/temporary-password").status_code == 405
    web_app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post(f"/faculty/courses/{course.id}/students/stu001/temporary-password")
    finally:
        web_app.config["WTF_CSRF_ENABLED"] = False
    assert resp.status_code == 400


def test_students_cannot_open_faculty_course_page(client, setup):
    _, course, _ = setup
    _login(client, "stu001")
    assert client.get(f"/faculty/courses/{course.id}").status_code == 403


def test_unknown_course_is_404(client, setup):
    _login(client)
    assert client.get("/faculty/courses/99999").status_code == 404


def test_confirm_script_is_served(client):
    assert b"data-confirm" in client.get("/static/js/confirm-forms.js").data
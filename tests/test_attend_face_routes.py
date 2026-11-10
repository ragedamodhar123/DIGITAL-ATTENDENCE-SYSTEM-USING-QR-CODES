"""Module 4, part 4: the face-check endpoint and a full class, end to end over HTTP."""
import base64
import re

import pytest

from app.extensions import db
from app.models import AttendanceRecord, AttendanceSession, FaceEmbedding, Role
from app.services import face_service, qr_service
from app.services.face_engine import EMBEDDING_DIM, MODEL_NAME
from tests.face_helpers import FakeEngine, encode, near, photo, unit
from tests.factories import TEST_PASSWORD, assign_faculty, enroll, make_course, make_user

FACES = {"stu000": unit(100), "stu001": unit(101), "stu002": unit(102)}


def _data_url(seed=0):
    return "data:image/jpeg;base64," + base64.b64encode(encode(photo(seed=seed))).decode()


class FaceOf(FakeEngine):
    """Stand-in camera: every photo 'shows' the face of whoever we say is in front of it."""

    def __init__(self):
        super().__init__()
        self.person = None

    def embed(self, image, detection):
        return near(FACES[self.person], seed=3)


@pytest.fixture
def engine(web_app):
    fake = FaceOf()
    web_app.extensions["face_engine"] = fake
    yield fake
    web_app.extensions.pop("face_engine", None)


@pytest.fixture
def klass():
    faculty = make_user(role=Role.FACULTY, username="fac001", must_change_password=False)
    course = make_course(code="DS301", name="Data Structures")
    assign_faculty(course, faculty)
    for username, vector in FACES.items():
        student = make_user(role=Role.STUDENT, username=username, must_change_password=False)
        enroll(course, student)
        db.session.add(FaceEmbedding(student=student, model_name=MODEL_NAME, dim=EMBEDDING_DIM, embedding=face_service.to_bytes(vector)))
    db.session.commit()
    return course


def _login(client, username):
    client.post("/login", data={"username": username, "password": TEST_PASSWORD})


def _scan(client, session_id):
    session = db.session.get(AttendanceSession, session_id)
    resp = client.get(f"/a/{qr_service.make_token(session)[0]}")
    assert resp.status_code == 200, resp.data
    return resp


def test_full_class_end_to_end(web_app, klass, engine):
    faculty = web_app.test_client()
    _login(faculty, "fac001")
    start = faculty.post(f"/faculty/courses/{klass.id}/attendance/start")
    session_id = int(start.headers["Location"].rsplit("/", 1)[1])

    # stu000 and stu001 attend; stu002 does not.
    for username in ("stu000", "stu001"):
        phone = web_app.test_client()
        _login(phone, username)
        page = _scan(phone, session_id).data.decode()
        assert 'id="attend-face"' in page and "attend-face.js" in page
        engine.person = username
        resp = phone.post(f"/api/attendance/sessions/{session_id}/face", json={"image": _data_url()})
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["status"] == "present" and body["course"] == "DS301 Data Structures"
        assert re.match(r"^\d{2}:\d{2}$", body["marked_at"])

    live = faculty.get(f"/api/faculty/sessions/{session_id}/live").get_json()
    assert live["counts"] == {"expected": 3, "present": 2, "not_yet": 1, "absent": 0}
    assert {r["username"] for r in live["recent"]} == {"STU000", "STU001"}

    faculty.post(f"/faculty/sessions/{session_id}/stop")
    records = {r.student.username: (r.status, r.method) for r in AttendanceRecord.query.filter_by(session_id=session_id)}
    assert records == {
        "stu000": ("present", "qr_face"),
        "stu001": ("present", "qr_face"),
        "stu002": ("absent", "auto_absent"),
    }


def test_friend_holding_up_their_face_to_my_phone_fails(web_app, klass, engine, client):
    faculty = web_app.test_client()
    _login(faculty, "fac001")
    session_id = int(faculty.post(f"/faculty/courses/{klass.id}/attendance/start").headers["Location"].rsplit("/", 1)[1])
    _login(client, "stu002")          # stu002 is absent; their phone is with a friend
    _scan(client, session_id)
    engine.person = "stu000"          # the friend's face is in front of the camera
    resp = client.post(f"/api/attendance/sessions/{session_id}/face", json={"image": _data_url()})
    body = resp.get_json()
    assert resp.status_code == 422 and body["reason"] == "face_mismatch" and body["attempts_left"] == 2
    assert AttendanceRecord.query.count() == 0


def test_face_check_without_scanning_first(web_app, klass, engine, client):
    faculty = web_app.test_client()
    _login(faculty, "fac001")
    session_id = int(faculty.post(f"/faculty/courses/{klass.id}/attendance/start").headers["Location"].rsplit("/", 1)[1])
    _login(client, "stu000")
    engine.person = "stu000"
    resp = client.post(f"/api/attendance/sessions/{session_id}/face", json={"image": _data_url()})
    assert resp.status_code == 409 and resp.get_json()["reason"] == "no_checkin"


def test_bad_input_and_permissions(web_app, klass, engine, client):
    _login(client, "stu000")
    assert client.post("/api/attendance/sessions/1/face", json={}).status_code == 422
    assert client.post("/api/attendance/sessions/1/face", json={"image": "nope"}).get_json()["reason"] == "unreadable_image"
    faculty = web_app.test_client()
    _login(faculty, "fac001")
    assert faculty.post("/api/attendance/sessions/1/face", json={"image": _data_url()}).status_code == 403
    assert web_app.test_client().post("/api/attendance/sessions/1/face", json={"image": _data_url()}).status_code == 401


def test_face_check_requires_csrf_header(web_app, klass, engine, client):
    _login(client, "stu000")
    web_app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post("/api/attendance/sessions/1/face", json={"image": _data_url()})
    finally:
        web_app.config["WTF_CSRF_ENABLED"] = False
    assert resp.status_code == 400 and resp.get_json()["error"] == "csrf_failed"


def test_face_photos_are_rate_limited(klass, engine, client):
    _login(client, "stu000")
    codes = [client.post("/api/attendance/sessions/1/face", json={}).status_code for _ in range(21)]
    assert codes[:20] == [422] * 20 and codes[20] == 429


def test_check_in_page_script_is_served(client):
    body = client.get("/static/js/attend-face.js").data
    assert b"attempts_left" in body and b"checkin_expired" in body
    assert b"AttendanceCamera" in client.get("/static/js/camera.js").data
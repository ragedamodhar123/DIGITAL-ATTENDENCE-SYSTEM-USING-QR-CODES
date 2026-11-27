"""Module 3, part 2: face registration page and its JSON endpoints."""
import base64
import re

import pytest

from app.extensions import db
from app.models import FaceEmbedding, Role
from tests.face_helpers import FakeEngine, encode, face, near, photo, unit
from tests.factories import TEST_PASSWORD, make_user


@pytest.fixture
def engine(web_app):
    """Install a stand-in face engine on the web app for one test."""
    fake = FakeEngine(embeddings=[near(unit(5), seed=s) for s in range(1, 20)])
    web_app.extensions["face_engine"] = fake
    yield fake
    web_app.extensions.pop("face_engine", None)


def _data_url(seed=0, **kwargs):
    return "data:image/jpeg;base64," + base64.b64encode(encode(photo(seed=seed, **kwargs))).decode()


def _student(client, username="stu001", must_change_password=False):
    student = make_user(role=Role.STUDENT, username=username, must_change_password=must_change_password)
    db.session.commit()
    client.post("/login", data={"username": username, "password": TEST_PASSWORD})
    return student


# ---- page ------------------------------------------------------------------

def test_new_student_sees_camera_page(client, engine):
    _student(client)
    page = client.get("/face/enroll").data.decode()
    assert 'id="face-enroll"' in page and 'data-captures="3"' in page
    assert "face-enroll.js" in page and "api.js" in page and "camera.js" in page
    assert 'name="csrf-token"' in page


def test_registered_student_sees_done_state(client, engine):
    _student(client)
    client.post("/api/face/enroll", json={"images": [_data_url(i) for i in range(3)]})
    page = client.get("/face/enroll").data.decode()
    assert "Your face is registered" in page and "face-enroll.js" not in page


def test_page_requires_password_change_first(client):
    _student(client, must_change_password=True)
    assert client.get("/face/enroll").headers["Location"] == "/change-password"


def test_faculty_cannot_open_face_page(client):
    make_user(role=Role.FACULTY, username="fac001", must_change_password=False)
    db.session.commit()
    client.post("/login", data={"username": "fac001", "password": TEST_PASSWORD})
    assert client.get("/face/enroll").status_code == 403


def test_camera_allowed_only_for_this_site(client):
    assert client.get("/login").headers["Permissions-Policy"] == "camera=(self), microphone=(), geolocation=()"


# ---- single-photo check ----------------------------------------------------

def test_good_photo_passes_check(client, engine):
    _student(client)
    resp = client.post("/api/face/check", json={"image": _data_url()})
    assert resp.status_code == 200 and resp.get_json() == {"ok": True}


def test_bad_photo_gets_reason_and_guidance(client, engine):
    _student(client)
    engine.detections = [face(size=50)]
    resp = client.post("/api/face/check", json={"image": _data_url()})
    body = resp.get_json()
    assert resp.status_code == 422
    assert body["reason"] == "face_too_small" and "Move closer" in body["message"]


@pytest.mark.parametrize(
    "payload,field",
    [(None, "_body"), ({}, "image"), ({"image": "not a data url"}, "image"), ({"image": "x", "extra": 1}, "extra")],
)
def test_check_rejects_bad_input(client, engine, payload, field):
    _student(client)
    resp = client.post("/api/face/check", json=payload) if payload is not None else client.post("/api/face/check")
    assert resp.status_code == 422
    assert field in resp.get_json()["details"]


def test_check_needs_login_and_finished_password_change(client, engine):
    assert client.post("/api/face/check", json={"image": _data_url()}).status_code == 401
    _student(client, must_change_password=True)
    resp = client.post("/api/face/check", json={"image": _data_url()})
    assert resp.status_code == 403 and resp.get_json()["error"] == "password_change_required"


# ---- registration ----------------------------------------------------------

def test_register_then_student_pages_open(client, engine):
    student = _student(client)
    assert client.get("/student/").headers["Location"] == "/face/enroll"
    resp = client.post("/api/face/enroll", json={"images": [_data_url(i) for i in range(3)]})
    assert resp.status_code == 201
    assert resp.get_json() == {"registered": True, "next": "/student/"}
    assert FaceEmbedding.query.filter_by(student_id=student.id).count() == 1
    assert client.get("/student/").status_code == 200


def test_register_twice_is_409(client, engine):
    _student(client)
    client.post("/api/face/enroll", json={"images": [_data_url(i) for i in range(3)]})
    resp = client.post("/api/face/enroll", json={"images": [_data_url(i) for i in range(10, 13)]})
    assert resp.status_code == 409


def test_wrong_number_of_photos(client, engine):
    _student(client)
    resp = client.post("/api/face/enroll", json={"images": [_data_url(0), _data_url(1)]})
    assert resp.status_code == 422 and "images" in resp.get_json()["details"]


def test_one_bad_photo_reported_by_position(client, engine):
    _student(client)
    images = [_data_url(0), _data_url(1, brightness=10), _data_url(2)]
    resp = client.post("/api/face/enroll", json={"images": images})
    assert resp.status_code == 422
    assert set(resp.get_json()["details"]["images"]) == {"1"}
    assert FaceEmbedding.query.count() == 0


def test_unreadable_data_url_in_registration(client, engine):
    _student(client)
    resp = client.post("/api/face/enroll", json={"images": ["data:text/plain;base64,aGk=", _data_url(1), _data_url(2)]})
    assert resp.status_code == 422 and resp.get_json()["reason"] == "unreadable_image"


def test_face_service_down_is_503(client, web_app, tmp_path):
    web_app.extensions.pop("face_engine", None)
    original = web_app.config["FACE_MODEL_DIR"]
    web_app.config["FACE_MODEL_DIR"] = str(tmp_path)  # no models here
    try:
        _student(client)
        resp = client.post("/api/face/check", json={"image": _data_url()})
    finally:
        web_app.config["FACE_MODEL_DIR"] = original
        web_app.extensions.pop("face_engine", None)
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "face_service_unavailable"


# ---- CSRF and rate limits ----------------------------------------------------

def test_endpoints_require_csrf_header_when_enabled(client, web_app, engine):
    _student(client)
    web_app.config["WTF_CSRF_ENABLED"] = True
    try:
        no_token = client.post("/api/face/check", json={"image": _data_url()})
        token = re.search(r'name="csrf-token" content="([^"]+)"', client.get("/face/enroll").data.decode()).group(1)
        with_token = client.post("/api/face/check", json={"image": _data_url()}, headers={"X-CSRFToken": token})
    finally:
        web_app.config["WTF_CSRF_ENABLED"] = False
    assert no_token.status_code == 400 and no_token.get_json()["error"] == "csrf_failed"
    assert with_token.status_code == 200


def test_registration_attempts_are_rate_limited(client, engine):
    _student(client)
    codes = [client.post("/api/face/enroll", json={"images": [_data_url(0)]}).status_code for _ in range(11)]
    assert codes[:10] == [422] * 10 and codes[10] == 429
    assert "Wait a few minutes" in client.post("/api/face/enroll", json={"images": []}).get_json()["message"]


def test_static_scripts_are_served(client):
    assert b"postJSON" in client.get("/static/js/api.js").data
    assert b"AttendanceCamera" in client.get("/static/js/face-enroll.js").data
    assert b"getUserMedia" in client.get("/static/js/camera.js").data  # shared camera code
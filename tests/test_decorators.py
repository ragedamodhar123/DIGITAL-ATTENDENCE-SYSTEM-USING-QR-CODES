"""Module 1, part 2: route protection, admin token, error responses.

The real pages arrive in part 3, so these tests build a small app with dummy
routes that use the decorators exactly the way real routes will.
"""
import struct

import pytest
from flask import Blueprint, abort, g, jsonify

from app import create_app
from app.extensions import db
from app.models import FaceEmbedding, Role
from app.services.errors import ValidationFailed
from app.utils.decorators import admin_token_required, role_required
from app.utils.security import generate_api_token
from tests.factories import make_user


def _stub():
    return "stub page"


@pytest.fixture(scope="module")
def gated_app(app):
    gapp = create_app("testing")
    gapp.config["PROPAGATE_EXCEPTIONS"] = False  # let the 500 handler run
    bp = Blueprint("gtest", __name__)

    @bp.get("/student-page")
    @role_required(Role.STUDENT)
    def student_page():
        return "student ok"

    @bp.get("/faculty-page")
    @role_required(Role.FACULTY)
    def faculty_page():
        return "faculty ok"

    @bp.get("/change-password-page")
    @role_required(Role.STUDENT, Role.FACULTY, allow_pending_password=True)
    def change_password_page():
        return "change ok"

    @bp.get("/face-enroll-page")
    @role_required(Role.STUDENT, allow_without_face=True)
    def face_enroll_page():
        return "enroll ok"

    @bp.get("/api/student-data")
    @role_required(Role.STUDENT)
    def student_api():
        return jsonify(ok=True)

    @bp.post("/api/student-post")
    @role_required(Role.STUDENT)
    def student_post():
        return jsonify(ok=True)

    @bp.get("/api/admin/ping")
    @admin_token_required
    def admin_ping():
        return jsonify(admin=g.admin.username)

    @bp.get("/api/fail-validation")
    def fail_validation():
        raise ValidationFailed("Bad input.", details={"name": ["Required."]})

    @bp.get("/html/fail-validation")
    def fail_validation_html():
        raise ValidationFailed("Bad <b>input</b>.")

    @bp.get("/api/missing")
    def api_missing():
        abort(404)

    @bp.get("/api/boom")
    def api_boom():
        raise RuntimeError("secret internal detail")

    @bp.get("/html/boom")
    def html_boom():
        raise RuntimeError("secret internal detail")

    gapp.register_blueprint(bp)
    # Stand-ins for pages that later parts create.
    for endpoint, rule in [
        ("auth.login", "/login"),
        ("auth.change_password", "/change-password"),
        ("face.enroll", "/face/enroll"),
    ]:
        if endpoint not in gapp.view_functions:
            gapp.add_url_rule(rule, endpoint, _stub)
    return gapp


@pytest.fixture
def gclient(gated_app):
    return gated_app.test_client()


def _commit_user(**kwargs):
    user = make_user(**kwargs)
    db.session.commit()  # requests run in their own session; data must be committed
    return user


def _ready_student(**kwargs):
    """A student who has changed their password and enrolled a face."""
    student = make_user(role=Role.STUDENT, must_change_password=False, **kwargs)
    db.session.add(
        FaceEmbedding(student=student, model_name="test", dim=128, embedding=struct.pack("<128f", *[0.0] * 128))
    )
    db.session.commit()
    return student


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = user.get_id()
        sess["_fresh"] = True


# ---- not logged in -------------------------------------------------------

def test_logged_out_page_redirects_to_login_and_remembers_page(gclient):
    resp = gclient.get("/student-page?tab=2")
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("/login?next=")
    assert "student-page" in resp.headers["Location"]


def test_logged_out_api_gets_json_401(gclient):
    resp = gclient.get("/api/student-data")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "login_required"


def test_logged_out_post_does_not_remember_next(gclient):
    resp = gclient.post("/api/student-post")
    assert resp.status_code == 401


# ---- password change gate -----------------------------------------------

def test_pending_password_redirects_to_change_page(gclient):
    student = _commit_user(role=Role.STUDENT)  # must_change_password=True by default
    _login(gclient, student)
    resp = gclient.get("/student-page")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/change-password"


def test_pending_password_api_gets_json_403(gclient):
    _login(gclient, _commit_user(role=Role.STUDENT))
    resp = gclient.get("/api/student-data")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "password_change_required"


def test_change_password_page_reachable_while_pending(gclient):
    _login(gclient, _commit_user(role=Role.STUDENT))
    assert gclient.get("/change-password-page").data == b"change ok"


def test_new_student_flow_has_no_redirect_loop(gclient):
    # Brand-new student: pending password AND no face. Every redirect must lead
    # somewhere they can actually open.
    student = _commit_user(role=Role.STUDENT)
    _login(gclient, student)
    assert gclient.get("/student-page").headers["Location"] == "/change-password"
    assert gclient.get("/change-password-page").status_code == 200
    student.must_change_password = False
    db.session.commit()
    assert gclient.get("/student-page").headers["Location"] == "/face/enroll"
    assert gclient.get("/face-enroll-page").status_code == 200


def test_password_gate_applies_to_faculty_too(gclient):
    _login(gclient, _commit_user(role=Role.FACULTY))
    assert gclient.get("/faculty-page").headers["Location"] == "/change-password"


# ---- role check ----------------------------------------------------------

def test_student_cannot_open_faculty_page(gclient):
    _login(gclient, _ready_student())
    assert gclient.get("/faculty-page").status_code == 403


def test_faculty_cannot_use_student_api(gclient):
    _login(gclient, _commit_user(role=Role.FACULTY, must_change_password=False))
    resp = gclient.get("/api/student-data")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "forbidden"


def test_faculty_page_works_for_faculty(gclient):
    _login(gclient, _commit_user(role=Role.FACULTY, must_change_password=False))
    assert gclient.get("/faculty-page").data == b"faculty ok"


def test_invalid_role_name_fails_at_startup():
    with pytest.raises(ValueError):
        role_required("studnet")
    with pytest.raises(ValueError):
        role_required()


# ---- face enrolment gate -------------------------------------------------

def test_student_without_face_redirected_to_enrolment(gclient):
    _login(gclient, _commit_user(role=Role.STUDENT, must_change_password=False))
    assert gclient.get("/student-page").headers["Location"] == "/face/enroll"


def test_student_without_face_api_gets_json_403(gclient):
    _login(gclient, _commit_user(role=Role.STUDENT, must_change_password=False))
    resp = gclient.get("/api/student-data")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "face_enrollment_required"


def test_enrolment_page_reachable_without_face(gclient):
    _login(gclient, _commit_user(role=Role.STUDENT, must_change_password=False))
    assert gclient.get("/face-enroll-page").data == b"enroll ok"


def test_password_gate_comes_before_face_gate(gclient):
    _login(gclient, _commit_user(role=Role.STUDENT))  # pending password AND no face
    assert gclient.get("/face-enroll-page").headers["Location"] == "/change-password"


def test_fully_ready_student_gets_through(gclient):
    _login(gclient, _ready_student())
    assert gclient.get("/student-page").data == b"student ok"
    assert gclient.get("/api/student-data").get_json() == {"ok": True}


# ---- session invalidation ------------------------------------------------

def test_deactivated_user_is_logged_out_immediately(gclient):
    student = _ready_student()
    _login(gclient, student)
    assert gclient.get("/student-page").status_code == 200
    student.is_active = False
    db.session.commit()
    assert gclient.get("/student-page").status_code == 302  # back to login


def test_password_change_elsewhere_logs_out_this_device(gclient):
    student = _ready_student()
    _login(gclient, student)
    student.session_version += 1  # what change_password() does
    db.session.commit()
    assert gclient.get("/api/student-data").status_code == 401


def test_tampered_session_cookie_rejected(gclient):
    with gclient.session_transaction() as sess:
        sess["_user_id"] = "not-an-id"
    assert gclient.get("/api/student-data").status_code == 401


# ---- admin token ---------------------------------------------------------

def _commit_admin():
    token, token_hash = generate_api_token()
    admin = make_user(role=Role.ADMIN, username="admin1", api_token_hash=token_hash)
    db.session.commit()
    return admin, token


def test_admin_api_accepts_valid_token(gclient):
    _, token = _commit_admin()
    resp = gclient.get("/api/admin/ping", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.get_json() == {"admin": "admin1"}


@pytest.mark.parametrize("header", [None, "Bearer wrong", "Basic abc"])
def test_admin_api_rejects_missing_or_bad_token(gclient, header):
    _commit_admin()
    headers = {"Authorization": header} if header else {}
    resp = gclient.get("/api/admin/ping", headers=headers)
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "invalid_token"
    assert resp.headers["WWW-Authenticate"].startswith("Bearer")


def test_admin_api_ignores_login_cookie(gclient):
    # A logged-in faculty member must not reach admin routes through their cookie.
    _commit_admin()
    _login(gclient, _commit_user(role=Role.FACULTY, must_change_password=False))
    assert gclient.get("/api/admin/ping").status_code == 401


# ---- error responses -----------------------------------------------------

def test_service_error_becomes_json_with_details(gclient):
    resp = gclient.get("/api/fail-validation")
    assert resp.status_code == 422
    assert resp.get_json() == {
        "error": "validation_failed",
        "message": "Bad input.",
        "details": {"name": ["Required."]},
    }


def test_service_error_on_page_is_escaped(gclient):
    resp = gclient.get("/html/fail-validation")
    assert resp.status_code == 422
    assert b"<b>input</b>" not in resp.data  # no HTML injection through messages


def test_api_404_is_json(gclient):
    resp = gclient.get("/api/missing")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


def test_unknown_api_url_is_json_404(gclient):
    resp = gclient.get("/api/does-not-exist")
    assert resp.status_code == 404
    assert resp.is_json


def test_api_crash_returns_generic_json_500(gclient):
    resp = gclient.get("/api/boom")
    assert resp.status_code == 500
    assert resp.get_json()["error"] == "server_error"
    assert b"secret internal detail" not in resp.data


def test_page_crash_returns_generic_500(gclient):
    resp = gclient.get("/html/boom")
    assert resp.status_code == 500
    assert b"secret internal detail" not in resp.data


def test_csrf_failure_on_api_is_json(gated_app, gclient):
    _login(gclient, _ready_student())
    gated_app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = gclient.post("/api/student-post")
    finally:
        gated_app.config["WTF_CSRF_ENABLED"] = False
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "csrf_failed"
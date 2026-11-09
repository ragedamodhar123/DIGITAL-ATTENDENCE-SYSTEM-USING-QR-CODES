"""Module 1, part 3: login, logout, change password, dashboards, route coverage."""
import re
import struct

import pytest

from app.extensions import db
from app.models import Enrollment, FaceEmbedding, Role, User
from tests.factories import TEST_PASSWORD, assign_faculty, enroll, make_course, make_user

NEW_PASSWORD = "Blue-River-42"


def _commit(obj):
    db.session.commit()
    return obj


def _student(username="stu001", ready=False, **extra):
    student = make_user(role=Role.STUDENT, username=username, must_change_password=not ready, **extra)
    if ready:
        db.session.add(
            FaceEmbedding(student=student, model_name="test", dim=128, embedding=struct.pack("<128f", *[0.0] * 128))
        )
    return _commit(student)


def _faculty(username="fac001", ready=True):
    return _commit(make_user(role=Role.FACULTY, username=username, must_change_password=not ready))


def _login(client, username, password=TEST_PASSWORD, **extra):
    data = {"username": username, "password": password}
    data.update(extra)
    return client.post("/login", data=data)


# ---- login page ----------------------------------------------------------

def test_login_page_renders(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b'name="username"' in resp.data and b'name="password"' in resp.data
    assert b'autocomplete="current-password"' in resp.data


def test_root_sends_logged_out_user_to_login(client):
    resp = client.get("/")
    assert resp.status_code == 302 and resp.headers["Location"].startswith("/login")


def test_new_student_login_goes_to_change_password(client):
    _student()
    resp = _login(client, "stu001")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/change-password"


def test_ready_faculty_login_goes_to_dashboard(client):
    _faculty()
    resp = _login(client, "FAC001")  # case-insensitive
    assert resp.headers["Location"] == "/"
    assert client.get("/").headers["Location"] == "/faculty/"
    assert client.get("/faculty/").status_code == 200


def test_login_returns_to_safe_next(client):
    _faculty()
    resp = _login(client, "fac001", next="/faculty/?tab=2")
    assert resp.headers["Location"] == "/faculty/?tab=2"


@pytest.mark.parametrize("bad_next", ["https://evil.com", "//evil.com", "/\\evil.com"])
def test_login_ignores_unsafe_next(client, bad_next):
    _faculty()
    resp = _login(client, "fac001", next=bad_next)
    assert resp.headers["Location"] == "/"


def test_failed_login_shows_generic_error_and_keeps_username(client):
    _faculty()
    resp = _login(client, "fac001", "Wrong-pass-9")
    assert resp.status_code == 401
    assert b"Invalid username or password." in resp.data
    assert b'value="fac001"' in resp.data
    assert b"Wrong-pass-9" not in resp.data  # password never echoed back


def test_unknown_user_gets_identical_page(client):
    resp = _login(client, "nobody99", "Wrong-pass-9")
    assert resp.status_code == 401
    assert b"Invalid username or password." in resp.data


def test_username_echo_is_escaped(client):
    resp = _login(client, '"><script>alert(1)</script>', "x")
    assert b"<script>alert(1)</script>" not in resp.data


def test_logged_in_user_visiting_login_is_sent_home(client):
    _faculty()
    _login(client, "fac001")
    assert client.get("/login").headers["Location"] == "/"


def test_remember_me_sets_long_lived_cookie_only_when_ticked(client):
    _faculty()
    _login(client, "fac001")
    assert client.get_cookie("remember_token") is None
    client.post("/logout")
    _login(client, "fac001", remember="on")
    assert client.get_cookie("remember_token") is not None


def test_admin_cannot_log_in_through_the_page(client):
    _commit(make_user(role=Role.ADMIN, username="admin1"))
    assert _login(client, "admin1").status_code == 401


# ---- rate limiting -------------------------------------------------------

def test_eleventh_rapid_attempt_for_one_username_is_blocked(client):
    _faculty()
    codes = [_login(client, "fac001", "Wrong-pass-9").status_code for _ in range(11)]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429
    blocked = _login(client, "fac001", TEST_PASSWORD)  # even the right password waits
    assert blocked.status_code == 429
    assert b"Too many login attempts" in blocked.data


def test_one_blocked_username_does_not_block_classmates_on_same_ip(client):
    _faculty()
    _student(username="stu002", ready=True)
    for _ in range(11):
        _login(client, "fac001", "Wrong-pass-9")
    resp = _login(client, "stu002")
    assert resp.status_code == 302


# ---- logout --------------------------------------------------------------

def test_logout_requires_post(client):
    _faculty()
    _login(client, "fac001")
    assert client.get("/logout").status_code == 405
    assert client.get("/faculty/").status_code == 200  # still logged in


def test_logout_ends_session(client):
    _faculty()
    _login(client, "fac001")
    resp = client.post("/logout")
    assert resp.headers["Location"] == "/login"
    assert client.get("/faculty/").status_code == 302


def test_logout_with_keep_me_logged_in_really_logs_out(client):
    # Regression: logout used to leave the remember-me cookie alive, so the next
    # request logged the user straight back in.
    _faculty()
    _login(client, "fac001", remember="on")
    assert client.get_cookie("remember_token") is not None

    resp = client.post("/logout")
    assert resp.headers["Location"] == "/login"
    assert client.get_cookie("remember_token") is None

    login_page = client.get("/login")
    assert login_page.status_code == 200  # shows the form, not a redirect home
    assert client.get("/faculty/").headers["Location"].startswith("/login")
    assert client.get("/").headers["Location"].startswith("/login")


def test_logout_after_password_change_with_remember_me(client):
    # Change password re-logs in with remember-me kept; logout must still end it.
    _student()
    _login(client, "stu001", remember="on")
    _change(client)
    client.post("/logout")
    assert client.get_cookie("remember_token") is None
    assert client.get("/face/enroll").headers["Location"].startswith("/login")


def test_logout_works_before_password_change(client):
    _student()
    _login(client, "stu001")
    assert client.post("/logout").headers["Location"] == "/login"


# ---- change password -----------------------------------------------------

def _change(client, current=TEST_PASSWORD, new=NEW_PASSWORD, confirm=None):
    return client.post(
        "/change-password",
        data={"current_password": current, "new_password": new, "confirm_password": confirm or new},
    )


def test_first_login_page_says_set_your_password(client):
    _student()
    _login(client, "stu001")
    resp = client.get("/change-password")
    assert b"Set your password" in resp.data and b"Temporary password" in resp.data


def test_first_login_full_flow_ends_at_face_enrolment(client):
    _student()
    _login(client, "stu001")
    resp = _change(client)
    assert resp.status_code == 302 and resp.headers["Location"] == "/"
    # Still logged in with the new session version, now gated on face enrolment.
    assert client.get("/").headers["Location"] == "/face/enroll"
    assert client.get("/student/").headers["Location"] == "/face/enroll"
    assert client.get("/face/enroll").status_code == 200

    user = User.query.filter_by(username="stu001").one()
    db.session.refresh(user)
    assert user.must_change_password is False


def test_invalid_change_shows_field_errors_and_saves_nothing(client):
    student = _student()
    _login(client, "stu001")
    resp = _change(client, current="wrong", new="short", confirm="other")
    assert resp.status_code == 422
    assert b"Current password is incorrect." in resp.data
    assert b"Passwords do not match." in resp.data
    assert b"is-invalid" in resp.data
    db.session.refresh(student)
    assert student.must_change_password is True and student.session_version == 1


def test_password_change_signs_out_other_devices(web_app):
    _student()
    phone, laptop = web_app.test_client(), web_app.test_client()
    _login(phone, "stu001")
    _login(laptop, "stu001")
    _change(phone)
    assert phone.get("/face/enroll").status_code == 200
    assert laptop.get("/face/enroll").headers["Location"].startswith("/login")


def test_change_password_keeps_remember_me_choice(client):
    _student()
    _login(client, "stu001", remember="on")
    _change(client)
    assert client.get_cookie("remember_token") is not None


def test_change_password_needs_login(client):
    assert client.get("/change-password").headers["Location"].startswith("/login")


# ---- dashboards ----------------------------------------------------------

def test_student_dashboard_lists_only_active_enrolments(client):
    student = _student(ready=True)
    active, dropped, retired = make_course(code="DS301"), make_course(code="OLD101"), make_course(code="RET999")
    enroll(active, student)
    db.session.add(Enrollment(course=dropped, student=student, is_active=False))
    retired.is_active = False
    enroll(retired, student)
    db.session.commit()

    _login(client, "stu001")
    page = client.get("/student/").data
    assert b"DS301" in page
    assert b"OLD101" not in page and b"RET999" not in page


def test_student_dashboard_empty_state(client):
    _student(ready=True)
    _login(client, "stu001")
    assert b"not enrolled in any courses yet" in client.get("/student/").data


def test_faculty_dashboard_shows_courses_and_counts(client):
    faculty = _faculty()
    course = make_course(code="DB302")
    assign_faculty(course, faculty)
    for i in range(3):
        enroll(course, make_user(role=Role.STUDENT, username=f"s{i:03d}x"))
    make_course(code="NOTMINE")
    db.session.commit()

    _login(client, "fac001")
    page = client.get("/faculty/").data.decode()
    assert "DB302" in page and "3 students" in page
    assert "NOTMINE" not in page


def test_student_cannot_open_faculty_dashboard(client):
    _student(ready=True)
    _login(client, "stu001")
    resp = client.get("/faculty/")
    assert resp.status_code == 403
    assert b"Not allowed" in resp.data


# ---- headers, CSRF, error pages -----------------------------------------

def test_security_headers_present(client):
    resp = client.get("/login")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"


def test_logged_in_pages_are_not_cached(client):
    _faculty()
    _login(client, "fac001")
    headers = client.get("/faculty/").headers
    assert "no-store" in headers["Cache-Control"]
    assert headers["Pragma"] == "no-cache"


def test_logged_in_pages_reload_when_restored_from_back_button_cache(client):
    _faculty()
    assert b"page-freshness.js" not in client.get("/login").data  # not needed when logged out
    _login(client, "fac001")
    assert b"page-freshness.js" in client.get("/faculty/").data


def test_logout_tells_browser_to_drop_cached_pages(client):
    _faculty()
    _login(client, "fac001")
    resp = client.post("/logout")
    assert resp.headers["Clear-Site-Data"] == '"cache"'
    assert "no-store" in resp.headers["Cache-Control"]


def test_freshness_script_is_served(client):
    resp = client.get("/static/js/page-freshness.js")
    assert resp.status_code == 200
    assert b"event.persisted" in resp.data


def test_login_form_requires_csrf_token_when_enabled(web_app, client):
    _faculty()
    web_app.config["WTF_CSRF_ENABLED"] = True
    try:
        no_token = _login(client, "fac001")
        assert no_token.status_code == 400
        assert b"expired" in no_token.data

        page = client.get("/login").data.decode()
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        ok = _login(client, "fac001", csrf_token=token)
        assert ok.status_code == 302
    finally:
        web_app.config["WTF_CSRF_ENABLED"] = False


def test_friendly_404_page(client):
    resp = client.get("/no-such-page")
    assert resp.status_code == 404
    assert b"Page not found" in resp.data


# ---- every route is protected -------------------------------------------

PUBLIC_ENDPOINTS = {"static", "health", "auth.login"}


def test_every_route_declares_who_may_use_it(app):
    unprotected = [
        endpoint
        for endpoint, view in app.view_functions.items()
        if endpoint not in PUBLIC_ENDPOINTS and not hasattr(view, "allowed_roles")
    ]
    assert unprotected == [], f"Routes without role_required/admin_token_required: {unprotected}"
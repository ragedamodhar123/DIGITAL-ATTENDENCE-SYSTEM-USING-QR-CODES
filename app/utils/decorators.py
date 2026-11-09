"""Route protection. Every route uses exactly one of these.

    @role_required(Role.STUDENT)                  student pages and student APIs
    @role_required(Role.FACULTY)                  faculty pages and APIs
    @role_required(Role.STUDENT, Role.FACULTY,
                   allow_pending_password=True)   the change-password page itself
    @role_required(Role.STUDENT,
                   allow_without_face=True)       the face-enrolment page itself
    @admin_token_required                         admin JSON API (Postman)

role_required checks, in this order:
1. logged in?                        no  -> login page (or JSON 401)
2. password change still pending?    yes -> change-password page (or JSON 403)
3. right role?                       no  -> 403
4. student with no enrolled face?    yes -> face-enrolment page (or JSON 403)
"""
from functools import wraps

from flask import abort, g, redirect, url_for
from flask_login import current_user

from ..extensions import db, login_manager
from ..models import FaceEmbedding, Role
from ..services.auth_service import admin_from_auth_header
from .http import current_path_for_next, json_error, wants_json

LOGIN_ENDPOINT = "auth.login"
CHANGE_PASSWORD_ENDPOINT = "auth.change_password"
FACE_ENROLL_ENDPOINT = "face.enroll"


def handle_unauthorized():
    """Called by Flask-Login whenever a logged-out user hits a protected route."""
    if wants_json():
        return json_error(401, "login_required", "Please log in.")
    next_url = current_path_for_next()
    if next_url:
        return redirect(url_for(LOGIN_ENDPOINT, next=next_url))
    return redirect(url_for(LOGIN_ENDPOINT))


def has_enrolled_face(user_id):
    """Cached for the rest of the request so it is queried at most once."""
    cache = g.setdefault("_face_enrolled", {})
    if user_id not in cache:
        cache[user_id] = (
            db.session.query(FaceEmbedding.id).filter_by(student_id=user_id).first() is not None
        )
    return cache[user_id]


def _send_elsewhere(code, message, endpoint):
    if wants_json():
        return json_error(403, code, message)
    return redirect(url_for(endpoint))


def role_required(*roles, allow_pending_password=False, allow_without_face=False):
    unknown = set(roles) - set(Role.ALL)
    if not roles or unknown:
        # A typo like role_required("studnet") must fail when the app starts,
        # not silently lock everyone out of a page.
        raise ValueError(f"role_required needs valid roles, got {roles!r}")

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()

            if current_user.must_change_password and not allow_pending_password:
                return _send_elsewhere(
                    "password_change_required",
                    "Please change your password before continuing.",
                    CHANGE_PASSWORD_ENDPOINT,
                )

            if current_user.role not in roles:
                if wants_json():
                    return json_error(403, "forbidden", "You do not have access to this.")
                abort(403)

            # Pages that allow a pending password (the change-password page) must also
            # work before face enrolment, or a brand-new student would loop between
            # "change your password" and "enrol your face" forever.
            needs_face_check = not (allow_without_face or allow_pending_password)
            if current_user.is_student and needs_face_check and not has_enrolled_face(current_user.id):
                return _send_elsewhere(
                    "face_enrollment_required",
                    "Please register your face before continuing.",
                    FACE_ENROLL_ENDPOINT,
                )

            return view(*args, **kwargs)

        wrapped.allowed_roles = tuple(roles)  # lets a test list every route's roles
        return wrapped

    return decorator


def admin_token_required(view):
    """Admin JSON API: requires 'Authorization: Bearer <token>'. Login cookies are ignored."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        from flask import request

        admin = admin_from_auth_header(request.headers.get("Authorization"))
        if admin is None:
            response, status = json_error(401, "invalid_token", "Missing or invalid API token.")
            response.headers["WWW-Authenticate"] = 'Bearer realm="admin-api"'
            return response, status
        g.admin = admin
        return view(*args, **kwargs)

    wrapped.allowed_roles = (Role.ADMIN,)
    return wrapped
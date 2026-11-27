"""JSON endpoints used by the face registration page (static/js/face-enroll.js).

These use the login cookie, so they are CSRF-protected: the page sends its token
in the X-CSRFToken header.
"""
from flask import current_app, jsonify, request, url_for
from flask_login import current_user

from ...extensions import limiter
from ...models import Role
from ...schemas.common import load_or_fail
from ...schemas.face_schemas import FaceCheckSchema, FaceEnrolSchema
from ...services import face_service
from ...utils.decorators import role_required
from ...utils.ratelimit import current_user_key
from . import api_bp


def _limit(config_key):
    return lambda: current_app.config[config_key]


@api_bp.post("/check")
@role_required(Role.STUDENT, allow_without_face=True)
@limiter.limit(_limit("FACE_CHECK_RATE_LIMIT"), key_func=current_user_key, error_message="Too many photos. Wait a few minutes.")
def check_photo():
    """Check ONE photo while the student is taking them, so problems show up immediately."""
    data = load_or_fail(FaceCheckSchema(), request.get_json(silent=True))
    image = face_service.decode_image(face_service.decode_data_url(data["image"]))
    face_service.analyse(image)  # raises FaceProblem with a reason the page can show
    return jsonify(ok=True)


@api_bp.post("/enroll")
@role_required(Role.STUDENT, allow_without_face=True)
@limiter.limit(_limit("FACE_ENROL_RATE_LIMIT"), key_func=current_user_key, error_message="Too many attempts. Wait a few minutes.")
def enroll():
    """Register the face from all photos. The server re-checks every photo itself."""
    data = load_or_fail(FaceEnrolSchema(), request.get_json(silent=True))
    images = [face_service.decode_data_url(url) for url in data["images"]]
    student = current_user._get_current_object()
    face_service.enrol(student, student, images)
    return jsonify(registered=True, next=url_for("student.dashboard")), 201
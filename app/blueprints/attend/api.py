"""Face check for attendance (static/js/attend-face.js). Cookie login + CSRF header."""
from flask import current_app, jsonify, request
from flask_login import current_user

from ...extensions import limiter
from ...models import Role
from ...schemas.common import load_or_fail
from ...schemas.face_schemas import FaceCheckSchema
from ...services import attendance_service, face_service
from ...utils.decorators import role_required
from ...utils.ratelimit import current_user_key
from ...utils.timeutils import to_local
from . import api_bp


@api_bp.post("/sessions/<int:session_id>/face")
@role_required(Role.STUDENT)
@limiter.limit(
    lambda: current_app.config["FACE_VERIFY_RATE_LIMIT"],
    key_func=current_user_key,
    error_message="Too many photos. Wait a few minutes and try again.",
)
def face_check(session_id):
    data = load_or_fail(FaceCheckSchema(), request.get_json(silent=True))
    image = face_service.decode_data_url(data["image"])
    result = attendance_service.complete_checkin(
        current_user._get_current_object(),
        session_id,
        image,
        ip=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )
    course = result.record.session.course
    return jsonify(
        status=result.status,
        course=f"{course.code} {course.name}",
        marked_at=to_local(result.record.marked_at).strftime("%H:%M"),
    )
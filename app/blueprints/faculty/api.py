"""JSON for the live attendance screen (static/js/faculty-session.js polls it)."""
from flask import current_app, jsonify, request
from flask_login import current_user

from ...extensions import db, limiter
from ...models import AttendanceRecord, AttendanceStatus, Role, SessionStatus, User
from ...services import qr_service, session_service
from ...utils.decorators import role_required
from ...utils.ratelimit import current_user_key
from ...utils.timeutils import to_local, utcnow
from . import api_bp

RECENT_LIMIT = 8


def _recent_present(session_id):
    rows = (
        db.session.query(User.full_name, User.username, AttendanceRecord.marked_at)
        .join(User, User.id == AttendanceRecord.student_id)
        .filter(AttendanceRecord.session_id == session_id, AttendanceRecord.status == AttendanceStatus.PRESENT)
        .order_by(AttendanceRecord.marked_at.desc(), AttendanceRecord.id.desc())
        .limit(RECENT_LIMIT)
        .all()
    )
    return [{"name": name, "username": username.upper(), "at": to_local(at).strftime("%H:%M")} for name, username, at in rows]


@api_bp.get("/sessions/<int:session_id>/live")
@role_required(Role.FACULTY)
@limiter.limit("120 per minute", key_func=current_user_key)
def live(session_id):
    now = utcnow()
    session = session_service.get_session_for_faculty(current_user, session_id, now=now)
    body = {
        "status": session.status,
        "counts": session_service.summary(session),
        "recent": _recent_present(session.id),
    }
    if session.status == SessionStatus.OPEN:
        token, refresh_in_ms = qr_service.make_token(session, now)
        body["qr"] = {
            "svg": qr_service.qr_svg(qr_service.scan_url(request.host_url, token)),
            "refresh_in_ms": refresh_in_ms,
            "rotation_ms": current_app.config["QR_ROTATION_SECONDS"] * 1000,
        }
        body["ends_at"] = to_local(session.expires_at).strftime("%H:%M")
    return jsonify(body)
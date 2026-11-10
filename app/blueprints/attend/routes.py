"""The page a student lands on after scanning the QR code: /a/<code>.

The check happens on GET, because the link is opened straight from a phone's
camera app. That is safe here: the code is unguessable, lives 10-20 seconds,
and only creates a pending check-in for the logged-in student, who must still
pass the face check.
"""
from flask import current_app, render_template, request
from flask_login import current_user

from ...extensions import limiter
from ...models import Role
from ...services import checkin_service, qr_service
from ...services.checkin_service import CheckinRefused
from ...utils.decorators import role_required
from ...utils.ratelimit import current_user_key
from . import bp


@bp.get("/a/<path:token>")
@role_required(Role.STUDENT)
@limiter.limit(
    lambda: current_app.config["SCAN_RATE_LIMIT"],
    key_func=current_user_key,
    error_message="Too many scans. Wait a few minutes and try again.",
)
def checkin(token):
    student = current_user._get_current_object()
    try:
        result = checkin_service.begin_checkin(
            student, token, ip=request.remote_addr, user_agent=request.headers.get("User-Agent")
        )
    except qr_service.TokenProblem as problem:
        return render_template("attend/checkin.html", problem=problem), problem.status_code
    except CheckinRefused as problem:
        return render_template("attend/checkin.html", problem=problem), problem.status_code
    return render_template(
        "attend/checkin.html",
        result=result,
        seconds_left=current_app.config["PENDING_CHECKIN_SECONDS"],
    )
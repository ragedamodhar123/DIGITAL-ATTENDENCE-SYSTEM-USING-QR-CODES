"""Face registration page."""
from flask import current_app, render_template
from flask_login import current_user

from ...models import Role
from ...utils.decorators import role_required
from . import bp


@bp.get("/enroll")
@role_required(Role.STUDENT, allow_without_face=True)
def enroll():
    return render_template(
        "face/enroll.html",
        already_registered=current_user.face is not None,
        captures_needed=current_app.config["FACE_ENROL_CAPTURES"],
    )
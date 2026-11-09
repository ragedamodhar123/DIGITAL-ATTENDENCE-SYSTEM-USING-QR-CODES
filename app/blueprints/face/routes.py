"""Face enrolment. Placeholder page until Module 3 builds the camera flow."""
from flask import render_template

from ...models import Role
from ...utils.decorators import role_required
from . import bp


@bp.get("/enroll")
@role_required(Role.STUDENT, allow_without_face=True)
def enroll():
    return render_template("face/enroll.html")
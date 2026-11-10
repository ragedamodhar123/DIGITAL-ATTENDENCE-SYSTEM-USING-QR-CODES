"""Student pages: home and the in-app QR scanner."""
from flask import render_template
from flask_login import current_user

from ...models import Role
from ...services.checkin_service import open_sessions_for_student
from ...services.course_service import courses_for_student
from ...utils.decorators import role_required
from . import bp


@bp.get("/")
@role_required(Role.STUDENT)
def dashboard():
    return render_template(
        "student/dashboard.html",
        courses=courses_for_student(current_user.id),
        open_sessions=open_sessions_for_student(current_user),
    )


@bp.get("/scan")
@role_required(Role.STUDENT)
def scan():
    return render_template("student/scan.html")
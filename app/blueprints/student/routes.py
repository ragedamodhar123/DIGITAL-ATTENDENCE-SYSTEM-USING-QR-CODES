"""Student pages: home (with attendance per course), course history, and the QR scanner."""
from flask import current_app, render_template
from flask_login import current_user

from ...models import Role
from ...services import student_attendance_service
from ...services.checkin_service import open_sessions_for_student
from ...utils.decorators import role_required
from . import bp


@bp.get("/")
@role_required(Role.STUDENT)
def dashboard():
    return render_template(
        "student/dashboard.html",
        summaries=student_attendance_service.course_summaries(current_user),
        open_sessions=open_sessions_for_student(current_user),
        threshold=current_app.config["ATTENDANCE_WARNING_PERCENT"],
    )


@bp.get("/courses/<int:course_id>")
@role_required(Role.STUDENT)
def course(course_id):
    summary, history = student_attendance_service.course_detail(current_user, course_id)
    return render_template(
        "student/course.html",
        summary=summary,
        history=history,
        threshold=current_app.config["ATTENDANCE_WARNING_PERCENT"],
    )


@bp.get("/scan")
@role_required(Role.STUDENT)
def scan():
    return render_template("student/scan.html")
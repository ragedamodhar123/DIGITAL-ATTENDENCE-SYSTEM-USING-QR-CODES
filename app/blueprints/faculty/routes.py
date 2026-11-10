"""Faculty pages. Attendance sessions are added in Module 4."""
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import limiter
from ...models import Role
from ...services import academic_service, face_service, user_service
from ...services.errors import ValidationFailed
from ...services.course_service import courses_for_faculty
from ...utils.decorators import role_required
from ...utils.ratelimit import current_user_key
from ...utils.validators import is_valid_username, normalize_username
from . import bp


@bp.get("/")
@role_required(Role.FACULTY)
def dashboard():
    return render_template("faculty/dashboard.html", courses=courses_for_faculty(current_user.id))


@bp.get("/courses/<int:course_id>")
@role_required(Role.FACULTY)
def course(course_id):
    # PermissionDenied / NotFound from the service become 403 / 404 pages.
    course, entries = academic_service.course_roster(current_user, course_id)
    return render_template("faculty/course.html", course=course, entries=entries)


def _student_in_course(course_id, username):
    """The active student `username` of this course (for this faculty member), or 404."""
    course, entries = academic_service.course_roster(current_user, course_id)
    if not is_valid_username(username):
        abort(404)
    wanted = normalize_username(username)
    student = next((e.student for e in entries if e.student.username == wanted), None)
    if student is None:
        abort(404)  # not an active student of THIS course
    return course, student


@bp.post("/courses/<int:course_id>/students/<username>/face-reset")
@role_required(Role.FACULTY)
@limiter.limit("30 per hour", key_func=current_user_key, error_message="Too many resets. Try again later.")
def reset_face(course_id, username):
    course, student = _student_in_course(course_id, username)
    try:
        face_service.reset_face(current_user, student.id, request.form.get("reason", ""))
    except ValidationFailed as exc:
        flash(next(iter(exc.details.values()))[0] if exc.details else exc.message, "error")
        return redirect(url_for("faculty.course", course_id=course.id))
    flash(f"Face registration reset for {student.full_name}. They will register again at their next login.", "success")
    return redirect(url_for("faculty.course", course_id=course.id))


@bp.post("/courses/<int:course_id>/students/<username>/temporary-password")
@role_required(Role.FACULTY)
@limiter.limit("30 per hour", key_func=current_user_key, error_message="Too many password resets. Try again later.")
def issue_temporary_password(course_id, username):
    course, student = _student_in_course(course_id, username)
    student, temp_password = user_service.issue_temporary_password(current_user, student.id)
    # Shown on this page only. Not flashed: flash messages live in the session
    # cookie, which is signed but readable, so the password would sit in the browser.
    return render_template(
        "faculty/temporary_password.html", course=course, student=student, temp_password=temp_password
    )
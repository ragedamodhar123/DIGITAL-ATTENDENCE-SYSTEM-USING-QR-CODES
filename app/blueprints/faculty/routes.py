"""Faculty pages. Attendance sessions are added in Module 4."""
from flask import abort, render_template
from flask_login import current_user

from ...extensions import limiter
from ...models import Role
from ...services import academic_service, user_service
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


@bp.post("/courses/<int:course_id>/students/<username>/temporary-password")
@role_required(Role.FACULTY)
@limiter.limit("30 per hour", key_func=current_user_key, error_message="Too many password resets. Try again later.")
def issue_temporary_password(course_id, username):
    course, entries = academic_service.course_roster(current_user, course_id)
    if not is_valid_username(username):
        abort(404)
    wanted = normalize_username(username)
    student = next((e.student for e in entries if e.student.username == wanted), None)
    if student is None:
        abort(404)  # not an active student of THIS course

    student, temp_password = user_service.issue_temporary_password(current_user, student.id)
    # Shown on this page only. Not flashed: flash messages live in the session
    # cookie, which is signed but readable, so the password would sit in the browser.
    return render_template(
        "faculty/temporary_password.html", course=course, student=student, temp_password=temp_password
    )
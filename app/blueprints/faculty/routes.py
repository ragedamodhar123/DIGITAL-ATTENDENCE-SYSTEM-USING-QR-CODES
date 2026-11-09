"""Faculty pages. Attendance sessions are added in Module 4."""
from flask import render_template
from flask_login import current_user

from ...models import Role
from ...services.course_service import courses_for_faculty
from ...utils.decorators import role_required
from . import bp


@bp.get("/")
@role_required(Role.FACULTY)
def dashboard():
    return render_template("faculty/dashboard.html", courses=courses_for_faculty(current_user.id))
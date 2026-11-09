"""Student pages. Scanning is added in Module 4."""
from flask import render_template
from flask_login import current_user

from ...models import Role
from ...services.course_service import courses_for_student
from ...utils.decorators import role_required
from . import bp


@bp.get("/")
@role_required(Role.STUDENT)
def dashboard():
    return render_template("student/dashboard.html", courses=courses_for_student(current_user.id))
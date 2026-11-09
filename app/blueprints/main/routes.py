"""The site root: sends each user to their own dashboard."""
from flask import redirect, url_for
from flask_login import current_user

from ...models import Role
from ...utils.decorators import role_required
from . import bp

HOME_BY_ROLE = {
    Role.STUDENT: "student.dashboard",
    Role.FACULTY: "faculty.dashboard",
}


@bp.get("/")
@role_required(Role.STUDENT, Role.FACULTY)
def home():
    return redirect(url_for(HOME_BY_ROLE[current_user.role]))
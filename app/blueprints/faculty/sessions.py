"""Faculty pages for running attendance: start, the live QR screen, stop."""
from urllib.parse import urlsplit

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...models import Role
from ...services import session_service
from ...services.errors import Conflict, ValidationFailed
from ...utils.decorators import role_required
from . import bp

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _first_message(exc):
    for messages in exc.details.values():
        if messages:
            return messages[0]
    return exc.message


@bp.post("/courses/<int:course_id>/attendance/start")
@role_required(Role.FACULTY)
def start_attendance(course_id):
    try:
        session, _created = session_service.start_session(current_user, course_id)
    except (ValidationFailed, Conflict) as exc:
        flash(_first_message(exc), "error")
        return redirect(url_for("faculty.course", course_id=course_id))
    return redirect(url_for("faculty.session", session_id=session.id))


@bp.get("/sessions/<int:session_id>")
@role_required(Role.FACULTY)
def session(session_id):
    attendance = session_service.get_session_for_faculty(current_user, session_id)
    # Phones cannot open "localhost": warn when the QR would point there.
    host = urlsplit(request.host_url).hostname
    return render_template(
        "faculty/session.html",
        attendance=attendance,
        course=attendance.course,
        counts=session_service.summary(attendance),
        local_address=host in LOCAL_HOSTS,
    )


@bp.post("/sessions/<int:session_id>/stop")
@role_required(Role.FACULTY)
def stop_attendance(session_id):
    session_service.stop_session(current_user, session_id)
    flash("Attendance stopped. Students not marked present are recorded as absent.", "success")
    return redirect(url_for("faculty.session", session_id=session_id))
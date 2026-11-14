"""Faculty pages for results and corrections: change records, history, other dates."""
from datetime import date

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import limiter
from ...models import Role
from ...services import manual_service
from ...services.errors import Conflict, ValidationFailed
from ...utils.decorators import role_required
from ...utils.ratelimit import current_user_key
from ...utils.timeutils import local_date
from ...utils.validators import is_valid_username, normalize_username
from . import bp


def _first_message(exc):
    """The most useful single message from a service error, for a flash."""
    for value in exc.details.values():
        if isinstance(value, list) and value:
            return value[0]
        if isinstance(value, dict):
            for name, messages in value.items():
                return f"{name.upper()}: {messages[0]}"
    return exc.message


def _summary_message(summary):
    parts = []
    if summary.changed:
        parts.append(f"Changed: {', '.join(n.upper() for n in summary.changed)}.")
    if summary.created:
        parts.append(f"Recorded: {', '.join(n.upper() for n in summary.created)}.")
    if summary.unchanged:
        parts.append(f"Already that status: {', '.join(n.upper() for n in summary.unchanged)}.")
    return " ".join(parts) or "Nothing changed."


@bp.post("/sessions/<int:session_id>/marks")
@role_required(Role.FACULTY)
@limiter.limit("60 per 10 minutes", key_func=current_user_key, error_message="Too many changes. Try again later.")
def change_marks(session_id):
    """Set one or more students to present/absent in this session, with one reason."""
    back = redirect(url_for("faculty.session", session_id=session_id) + "#roster")
    usernames = [normalize_username(u) for u in request.form.getlist("students") if is_valid_username(u)]
    status = request.form.get("status", "")
    if not usernames:
        flash("Choose at least one student.", "error")
        return back
    try:
        summary = manual_service.set_statuses(
            current_user, session_id, {name: status for name in usernames}, request.form.get("reason", "")
        )
    except (ValidationFailed, Conflict) as exc:
        flash(_first_message(exc), "error")
        return back
    flash(_summary_message(summary), "success")
    return back


@bp.get("/sessions/<int:session_id>/students/<username>/history")
@role_required(Role.FACULTY)
def record_history(session_id, username):
    record, history = manual_service.record_history(current_user, session_id, normalize_username(username))
    return render_template("faculty/history.html", record=record, history=history, attendance=record.session)


@bp.route("/courses/<int:course_id>/attendance/other-date", methods=["GET", "POST"])
@role_required(Role.FACULTY)
def other_date(course_id):
    """Record attendance for a date with no live session (e.g. roll taken on paper)."""
    course, sessions = manual_service.list_sessions(current_user, course_id)
    students = sorted(manual_service.active_students(course.id).values(), key=lambda u: u.username)
    today = local_date()
    form = {
        "session_date": request.form.get("session_date", today.isoformat()),
        "present": set(request.form.getlist("present")),
        "reason": request.form.get("reason", ""),
    }
    context = {"course": course, "students": students, "today": today, "form": form, "errors": {}, "same_day": False}

    if request.method == "GET":
        return render_template("faculty/other_date.html", **context)

    try:
        chosen = date.fromisoformat(form["session_date"])
    except ValueError:
        context["errors"] = {"session_date": ["Choose a valid date."]}
        return render_template("faculty/other_date.html", **context), 422

    try:
        session, _summary = manual_service.create_manual_session(
            current_user, course.id, chosen, sorted(form["present"]), form["reason"],
            allow_same_day=request.form.get("confirm_same_day") == "yes",
        )
    except Conflict as exc:
        # Attendance already exists on that date: ask to confirm it is another class.
        context.update(errors=exc.details, same_day=True)
        return render_template("faculty/other_date.html", **context), 409
    except ValidationFailed as exc:
        context["errors"] = exc.details
        return render_template("faculty/other_date.html", **context), 422

    present = len(form["present"])
    flash(f"Attendance saved for {chosen.strftime('%d %b %Y')}: {present} present, {len(students) - present} absent.", "success")
    return redirect(url_for("faculty.session", session_id=session.id))
"""Faculty report downloads (Excel / CSV) for a course."""
from datetime import date
from io import BytesIO

from flask import flash, redirect, request, send_file, url_for
from flask_login import current_user

from ...extensions import limiter
from ...models import Role
from ...services import report_service
from ...services.errors import ValidationFailed
from ...utils.decorators import role_required
from ...utils.ratelimit import current_user_key
from . import bp

FORMATS = {
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", report_service.to_xlsx),
    "csv": ("text/csv; charset=utf-8", report_service.to_csv),
}


def _parse_date(raw):
    """'' -> None; '2026-11-03' -> date; anything else -> ValueError."""
    raw = (raw or "").strip()
    return date.fromisoformat(raw) if raw else None


@bp.get("/courses/<int:course_id>/report")
@role_required(Role.FACULTY)
@limiter.limit("30 per 10 minutes", key_func=current_user_key, error_message="Too many downloads. Try again later.")
def course_report(course_id):
    back = redirect(url_for("faculty.course", course_id=course_id) + "#report")
    fmt = request.args.get("format", "xlsx")
    if fmt not in FORMATS:
        flash("Choose Excel or CSV.", "error")
        return back
    try:
        date_from = _parse_date(request.args.get("from"))
        date_to = _parse_date(request.args.get("to"))
    except ValueError:
        flash("Choose valid dates.", "error")
        return back
    try:
        report = report_service.build_course_report(current_user, course_id, date_from, date_to)
    except ValidationFailed as exc:
        flash(next(iter(exc.details.values()))[0] if exc.details else exc.message, "error")
        return back

    mimetype, render = FORMATS[fmt]
    return send_file(
        BytesIO(render(report)),
        mimetype=mimetype,
        as_attachment=True,
        download_name=report_service.file_name(report, fmt),
        max_age=0,
    )
"""Admin API: attendance results, corrections, history and other dates."""
from io import BytesIO

from flask import current_app, jsonify, send_file

from ...schemas.attendance_schemas import (
    AttendanceChangeSchema,
    ReportQuerySchema,
    AuditOutSchema,
    ManualSessionSchema,
    RosterRowOutSchema,
    SessionOutSchema,
)
from ...services import manual_service, report_service, session_service
from ...services.errors import NotFound
from ...utils.decorators import admin_token_required
from ...utils.validators import is_valid_username, normalize_username
from . import bp
from .helpers import admin, body, query

session_out = SessionOutSchema()
row_out = RosterRowOutSchema()


def _summary(summary):
    return {"changed": summary.changed, "created": summary.created, "unchanged": summary.unchanged}


@bp.get("/courses/<int:course_id>/sessions")
@admin_token_required
def course_sessions(course_id):
    course, sessions = manual_service.list_sessions(admin(), course_id)
    items = []
    for session, present, absent in sessions:
        item = session_out.dump(session)
        item.update(present=present, absent=absent)
        items.append(item)
    return jsonify(course={"id": course.id, "code": course.code, "term": course.term}, items=items, total=len(items))


@bp.get("/sessions/<int:session_id>")
@admin_token_required
def session_detail(session_id):
    session, roster = manual_service.session_roster(admin(), session_id)
    return jsonify(
        session=session_out.dump(session),
        counts=session_service.summary(session),
        students=row_out.dump(roster, many=True),
    )


@bp.post("/sessions/<int:session_id>/marks")
@admin_token_required
def change_marks(session_id):
    data = body(AttendanceChangeSchema())
    summary = manual_service.set_statuses(admin(), session_id, data["changes"], data["reason"])
    session, roster = manual_service.session_roster(admin(), session_id)
    return jsonify(summary=_summary(summary), counts=session_service.summary(session))


@bp.get("/sessions/<int:session_id>/students/<username>/history")
@admin_token_required
def record_history(session_id, username):
    if not is_valid_username(username):
        raise NotFound("No account with this username.")
    record, history = manual_service.record_history(admin(), session_id, normalize_username(username))
    return jsonify(
        student={"username": record.student.username, "full_name": record.student.full_name},
        status=record.status,
        method=record.method,
        history=AuditOutSchema().dump(history, many=True),
    )


@bp.post("/courses/<int:course_id>/sessions/manual")
@admin_token_required
def manual_session(course_id):
    data = body(ManualSessionSchema())
    session, summary = manual_service.create_manual_session(
        admin(), course_id, data["session_date"], data["present"], data["reason"],
        allow_same_day=data["allow_same_day"],
    )
    return jsonify(session=session_out.dump(session), counts=session_service.summary(session), summary=_summary(summary)), 201


FILE_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
}


def _report_json(report, threshold):
    return {
        "course": {"id": report.course.id, "code": report.course.code, "name": report.course.name, "term": report.course.term},
        "period": {
            "from": report.date_from.isoformat() if report.date_from else None,
            "to": report.date_to.isoformat() if report.date_to else None,
        },
        "threshold": threshold,
        "classes": [
            {"session_id": s.id, "session_date": s.session_date.isoformat(), "label": report.column_label(s), "kind": s.kind}
            for s in report.sessions
        ],
    }


@bp.get("/courses/<int:course_id>/report")
@admin_token_required
def course_report(course_id):
    """?format=json (default) | xlsx | csv, optional from/to dates and threshold (JSON only)."""
    options = query(ReportQuerySchema())
    report = report_service.build_course_report(admin(), course_id, options["date_from"], options["date_to"])
    if options["format"] in FILE_TYPES:
        render = report_service.to_xlsx if options["format"] == "xlsx" else report_service.to_csv
        return send_file(
            BytesIO(render(report)), mimetype=FILE_TYPES[options["format"]], as_attachment=True,
            download_name=report_service.file_name(report, options["format"]), max_age=0,
        )
    threshold = options["threshold"] or current_app.config["ATTENDANCE_WARNING_PERCENT"]
    body = _report_json(report, threshold)
    body["students"] = report_service.student_rows(report, threshold)
    return jsonify(body)


@bp.get("/courses/<int:course_id>/below-threshold")
@admin_token_required
def below_threshold(course_id):
    options = query(ReportQuerySchema())
    report = report_service.build_course_report(admin(), course_id, options["date_from"], options["date_to"])
    threshold = options["threshold"] or current_app.config["ATTENDANCE_WARNING_PERCENT"]
    body = _report_json(report, threshold)
    body["students"] = report_service.below_threshold(report, threshold)
    body["count"] = len(body["students"])
    return jsonify(body)
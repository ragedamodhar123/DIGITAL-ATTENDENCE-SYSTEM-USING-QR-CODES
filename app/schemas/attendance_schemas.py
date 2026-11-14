"""Input and output shapes for attendance results and corrections (admin API)."""
from marshmallow import fields, validate

from .common import StrictSchema, Username

STATUS = validate.OneOf(["present", "absent"], error="Use present or absent.")
MAX_CHANGES = 500


def _reason():
    return fields.String(required=True, validate=validate.Length(min=3, max=500))


class AttendanceChangeSchema(StrictSchema):
    """{"changes": {"stu001": "present", "stu002": "absent"}, "reason": "..."}"""

    changes = fields.Dict(
        keys=Username(), values=fields.String(validate=STATUS), required=True,
        validate=validate.Length(min=1, max=MAX_CHANGES),
    )
    reason = _reason()


class ManualSessionSchema(StrictSchema):
    """Attendance for a date with no live session."""

    session_date = fields.Date(required=True)  # "2026-11-03"
    present = fields.List(Username(), load_default=list, validate=validate.Length(max=MAX_CHANGES))
    reason = _reason()
    allow_same_day = fields.Boolean(load_default=False)


class SessionOutSchema(StrictSchema):
    id = fields.Integer()
    course_id = fields.Integer()
    session_date = fields.Date()
    kind = fields.String()          # live | manual
    status = fields.String()        # open | closed
    started_at = fields.DateTime()
    ended_at = fields.DateTime(allow_none=True)
    created_by = fields.Function(lambda s: s.created_by.username)


class RosterRowOutSchema(StrictSchema):
    username = fields.Function(lambda r: r.student.username)
    full_name = fields.Function(lambda r: r.student.full_name)
    section_code = fields.Function(lambda r: r.student.section.code if r.student.section else None)
    status = fields.Function(lambda r: r.status)  # present | absent | null (live class, not checked in yet)
    method = fields.Function(lambda r: r.record.method if r.record else None)  # qr_face | manual | auto_absent
    marked_at = fields.Function(lambda r: r.record.marked_at.isoformat() if r.record else None)
    marked_by = fields.Function(lambda r: r.record.marked_by.username if r.record and r.record.marked_by else None)
    face_distance = fields.Function(lambda r: r.record.face_distance if r.record else None)
    changes = fields.Integer()


class AuditOutSchema(StrictSchema):
    old_status = fields.String(allow_none=True)
    new_status = fields.String()
    reason = fields.String()
    by = fields.Function(lambda a: a.changed_by.username)
    at = fields.DateTime(attribute="changed_at")
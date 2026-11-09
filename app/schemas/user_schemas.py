"""Input and output shapes for user management."""
from marshmallow import fields, validate

from .common import OptionalEmail, QuerySchema, StrictSchema, Username, code_field, full_name_field

MAX_BULK_STUDENTS = 500


class FacultyCreateSchema(StrictSchema):
    username = Username(required=True)
    full_name = full_name_field()
    email = OptionalEmail(load_default=None)


class StudentCreateSchema(StrictSchema):
    username = Username(required=True)  # roll number
    full_name = full_name_field()
    email = OptionalEmail(load_default=None)
    section_code = code_field()


class StudentBulkCreateSchema(StrictSchema):
    students = fields.List(
        fields.Nested(StudentCreateSchema),
        required=True,
        validate=validate.Length(min=1, max=MAX_BULK_STUDENTS),
    )


class UserUpdateSchema(StrictSchema):
    """Editable fields. Username and role are deliberately not editable:
    the roll number is the student's identity in every attendance record."""

    full_name = full_name_field(required=False)
    email = OptionalEmail()
    section_code = code_field(required=False)


class UserListQuerySchema(QuerySchema):
    role = fields.String(validate=validate.OneOf(["faculty", "student"]))
    section_code = code_field(required=False)
    active = fields.Boolean()
    q = fields.String(validate=validate.Length(max=64))
    page = fields.Integer(load_default=1, validate=validate.Range(min=1))
    per_page = fields.Integer(load_default=25, validate=validate.Range(min=1, max=100))


class UserOutSchema(StrictSchema):
    """What the API returns about a user. Never includes hashes or tokens."""

    id = fields.Integer()
    username = fields.String()
    full_name = fields.String()
    email = fields.String(allow_none=True)
    role = fields.String()
    section_code = fields.Function(lambda u: u.section.code if u.section else None)
    is_active = fields.Boolean()
    must_change_password = fields.Boolean()
    face_enrolled = fields.Function(lambda u: u.face is not None if u.is_student else None)
    created_at = fields.DateTime()
    last_login_at = fields.DateTime(allow_none=True)
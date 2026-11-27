"""Input and output shapes for sections, courses, faculty assignment and enrolment.

People are referred to by username and sections by code (what an admin types in
Postman); courses by their id in the URL, because a course code repeats every term.
"""
from marshmallow import fields, validate

from .common import QuerySchema, StrictSchema, Username, code_field

MAX_BULK_ENROL = 500


def _name(max_length, required=False):
    return fields.String(required=required, validate=validate.Length(min=1, max=max_length))


# ---- sections --------------------------------------------------------------

class SectionCreateSchema(StrictSchema):
    code = code_field()
    name = _name(120, required=True)


class SectionUpdateSchema(StrictSchema):
    code = code_field(required=False)
    name = _name(120)


class SectionListQuerySchema(QuerySchema):
    active = fields.Boolean()
    q = fields.String(validate=validate.Length(max=64))


class SectionOutSchema(StrictSchema):
    id = fields.Integer()
    code = fields.String()
    name = fields.String()
    is_active = fields.Boolean()
    student_count = fields.Integer()


# ---- courses ---------------------------------------------------------------

class CourseCreateSchema(StrictSchema):
    code = code_field()
    name = _name(160, required=True)
    term = code_field(max_length=16)  # e.g. 2026-ODD


class CourseUpdateSchema(StrictSchema):
    code = code_field(required=False)
    name = _name(160)
    term = code_field(required=False, max_length=16)


class CourseListQuerySchema(QuerySchema):
    term = code_field(required=False, max_length=16)
    active = fields.Boolean()
    faculty = Username()  # courses taught by this faculty username
    q = fields.String(validate=validate.Length(max=64))
    page = fields.Integer(load_default=1, validate=validate.Range(min=1))
    per_page = fields.Integer(load_default=25, validate=validate.Range(min=1, max=100))


class CourseOutSchema(StrictSchema):
    id = fields.Integer()
    code = fields.String()
    name = fields.String()
    term = fields.String()
    is_active = fields.Boolean()
    faculty = fields.Function(lambda c: sorted(link.faculty.username for link in c.faculty_links))
    student_count = fields.Integer()


# ---- assignment and enrolment ---------------------------------------------

class FacultyAssignSchema(StrictSchema):
    username = Username(required=True)


class EnrolStudentsSchema(StrictSchema):
    usernames = fields.List(
        Username(), required=True, validate=validate.Length(min=1, max=MAX_BULK_ENROL)
    )


class EnrolSectionSchema(StrictSchema):
    section_code = code_field()


class RosterQuerySchema(QuerySchema):
    include_dropped = fields.Boolean(load_default=False)


class RosterEntryOutSchema(StrictSchema):
    username = fields.Function(lambda e: e.student.username)
    full_name = fields.Function(lambda e: e.student.full_name)
    section_code = fields.Function(lambda e: e.student.section.code if e.student.section else None)
    is_active = fields.Boolean()  # False = dropped from this course
    account_active = fields.Function(lambda e: e.student.is_active)
    enrolled_at = fields.DateTime(attribute="created_at")
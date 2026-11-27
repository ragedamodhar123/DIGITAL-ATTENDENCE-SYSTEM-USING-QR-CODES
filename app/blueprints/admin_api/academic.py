"""Admin API: sections, courses, faculty assignment, enrolment."""
from flask import jsonify

from ...schemas.academic_schemas import (
    CourseCreateSchema,
    CourseListQuerySchema,
    CourseOutSchema,
    CourseUpdateSchema,
    EnrolSectionSchema,
    EnrolStudentsSchema,
    FacultyAssignSchema,
    RosterEntryOutSchema,
    RosterQuerySchema,
    SectionCreateSchema,
    SectionListQuerySchema,
    SectionOutSchema,
    SectionUpdateSchema,
)
from ...services import academic_service as svc
from ...services.errors import NotFound
from ...utils.decorators import admin_token_required
from ...utils.validators import is_valid_username, normalize_username
from . import bp
from .helpers import admin, body, page_of, query

section_out = SectionOutSchema()
course_out = CourseOutSchema()
roster_out = RosterEntryOutSchema()


def _username_from_url(raw):
    if not is_valid_username(raw):
        raise NotFound("No account with this username.")
    return normalize_username(raw)


# ---- sections --------------------------------------------------------------

@bp.get("/sections")
@admin_token_required
def list_sections():
    sections = svc.list_sections(admin(), query(SectionListQuerySchema()))
    return jsonify(items=section_out.dump(sections, many=True), total=len(sections))


@bp.post("/sections")
@admin_token_required
def create_section():
    return jsonify(section=section_out.dump(svc.create_section(admin(), body(SectionCreateSchema())))), 201


@bp.patch("/sections/<int:section_id>")
@admin_token_required
def update_section(section_id):
    section = svc.update_section(admin(), section_id, body(SectionUpdateSchema()))
    return jsonify(section=section_out.dump(section))


@bp.post("/sections/<int:section_id>/retire")
@admin_token_required
def retire_section(section_id):
    return jsonify(section=section_out.dump(svc.set_section_active(admin(), section_id, False)))


@bp.post("/sections/<int:section_id>/restore")
@admin_token_required
def restore_section(section_id):
    return jsonify(section=section_out.dump(svc.set_section_active(admin(), section_id, True)))


# ---- courses ---------------------------------------------------------------

@bp.get("/courses")
@admin_token_required
def list_courses():
    filters = query(CourseListQuerySchema())
    courses, total = svc.list_courses(admin(), filters)
    return page_of(courses, total, filters, course_out)


@bp.post("/courses")
@admin_token_required
def create_course():
    return jsonify(course=course_out.dump(svc.create_course(admin(), body(CourseCreateSchema())))), 201


@bp.get("/courses/<int:course_id>")
@admin_token_required
def get_course(course_id):
    return jsonify(course=course_out.dump(svc.get_course(admin(), course_id)))


@bp.patch("/courses/<int:course_id>")
@admin_token_required
def update_course(course_id):
    return jsonify(course=course_out.dump(svc.update_course(admin(), course_id, body(CourseUpdateSchema()))))


@bp.post("/courses/<int:course_id>/retire")
@admin_token_required
def retire_course(course_id):
    return jsonify(course=course_out.dump(svc.set_course_active(admin(), course_id, False)))


@bp.post("/courses/<int:course_id>/restore")
@admin_token_required
def restore_course(course_id):
    return jsonify(course=course_out.dump(svc.set_course_active(admin(), course_id, True)))


# ---- faculty assignment ----------------------------------------------------

@bp.post("/courses/<int:course_id>/faculty")
@admin_token_required
def assign_faculty(course_id):
    data = body(FacultyAssignSchema())
    course, created = svc.assign_faculty(admin(), course_id, data["username"])
    return jsonify(course=course_out.dump(course), assigned=created), (201 if created else 200)


@bp.delete("/courses/<int:course_id>/faculty/<username>")
@admin_token_required
def unassign_faculty(course_id, username):
    course = svc.unassign_faculty(admin(), course_id, _username_from_url(username))
    return jsonify(course=course_out.dump(course))


# ---- enrolment -------------------------------------------------------------

@bp.post("/courses/<int:course_id>/enrolments")
@admin_token_required
def enrol_students(course_id):
    data = body(EnrolStudentsSchema())
    summary = svc.enrol_students(admin(), course_id, data["usernames"])
    return jsonify(summary=summary, course=course_out.dump(svc.get_course(admin(), course_id)))


@bp.post("/courses/<int:course_id>/enrolments/section")
@admin_token_required
def enrol_section(course_id):
    data = body(EnrolSectionSchema())
    summary = svc.enrol_section(admin(), course_id, data["section_code"])
    return jsonify(summary=summary, course=course_out.dump(svc.get_course(admin(), course_id)))


@bp.delete("/courses/<int:course_id>/enrolments/<username>")
@admin_token_required
def drop_student(course_id, username):
    svc.drop_student(admin(), course_id, _username_from_url(username))
    return jsonify(course=course_out.dump(svc.get_course(admin(), course_id)))


@bp.get("/courses/<int:course_id>/roster")
@admin_token_required
def course_roster(course_id):
    options = query(RosterQuerySchema())
    course, entries = svc.course_roster(admin(), course_id, options["include_dropped"])
    return jsonify(course=course_out.dump(svc.get_course(admin(), course_id)), students=roster_out.dump(entries, many=True))
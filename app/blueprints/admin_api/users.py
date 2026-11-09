"""Admin API: faculty and student accounts."""
from flask import jsonify

from ...schemas.user_schemas import (
    FacultyCreateSchema,
    StudentBulkCreateSchema,
    StudentCreateSchema,
    UserListQuerySchema,
    UserOutSchema,
    UserUpdateSchema,
)
from ...services import user_service
from ...utils.decorators import admin_token_required
from . import bp
from .helpers import admin, body, page_of, query

user_out = UserOutSchema()


def _with_password(user, temp_password):
    # The temporary password appears in this one response only; it is never stored.
    return {"user": user_out.dump(user), "temporary_password": temp_password}


@bp.post("/faculty")
@admin_token_required
def create_faculty():
    user, temp = user_service.create_faculty(admin(), body(FacultyCreateSchema()))
    return jsonify(_with_password(user, temp)), 201


@bp.post("/students")
@admin_token_required
def create_student():
    user, temp = user_service.create_student(admin(), body(StudentCreateSchema()))
    return jsonify(_with_password(user, temp)), 201


@bp.post("/students/bulk")
@admin_token_required
def bulk_create_students():
    data = body(StudentBulkCreateSchema())
    created = user_service.bulk_create_students(admin(), data["students"])
    return jsonify(created=[_with_password(u, t) for u, t in created], count=len(created)), 201


@bp.get("/users")
@admin_token_required
def list_users():
    filters = query(UserListQuerySchema())
    users, total = user_service.list_users(admin(), filters)
    return page_of(users, total, filters, user_out)


@bp.get("/users/<int:user_id>")
@admin_token_required
def get_user(user_id):
    return jsonify(user=user_out.dump(user_service.get_user(admin(), user_id)))


@bp.patch("/users/<int:user_id>")
@admin_token_required
def update_user(user_id):
    user = user_service.update_user(admin(), user_id, body(UserUpdateSchema()))
    return jsonify(user=user_out.dump(user))


@bp.post("/users/<int:user_id>/deactivate")
@admin_token_required
def deactivate_user(user_id):
    return jsonify(user=user_out.dump(user_service.set_active(admin(), user_id, False)))


@bp.post("/users/<int:user_id>/activate")
@admin_token_required
def activate_user(user_id):
    return jsonify(user=user_out.dump(user_service.set_active(admin(), user_id, True)))


@bp.post("/users/<int:user_id>/temporary-password")
@admin_token_required
def issue_temporary_password(user_id):
    user, temp = user_service.issue_temporary_password(admin(), user_id)
    return jsonify(_with_password(user, temp))
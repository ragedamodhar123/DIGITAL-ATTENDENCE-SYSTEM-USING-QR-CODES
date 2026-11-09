"""Creating and managing faculty and student accounts.

Every function takes `actor`: the User performing the action. Permission rules:
- Admin: create, edit, list, deactivate/reactivate faculty and students;
  issue temporary passwords to faculty and students.
- Faculty: issue temporary passwords ONLY to students actively enrolled in an
  active course they teach (a student who forgot their password, in class).
- Admin accounts are managed only with `flask create-admin`, never through the API.

Temporary passwords are returned once, never stored in plain text, and always
force a password change at next login.
"""
from flask import current_app
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import Course, CourseFaculty, Enrollment, Role, Section, User
from ..utils.security import generate_temporary_password, hash_password
from .errors import Conflict, NotFound, PermissionDenied, ValidationFailed

MANAGED_ROLES = (Role.FACULTY, Role.STUDENT)

# Which unique constraint failed -> which field to blame, in a readable way.
_UNIQUE_FIELDS = {
    "uq_users_username": ("username", "This username is already taken."),
    "uq_users_email": ("email", "This email is already used by another account."),
}


# ---- permission helpers ----------------------------------------------------

def _require_admin(actor):
    if actor is None or not actor.is_admin or not actor.is_active:
        raise PermissionDenied("Only the admin can do this.")


def _get_managed_user(user_id):
    # bool is a subclass of int in Python; True must not quietly mean user 1.
    valid_id = isinstance(user_id, int) and not isinstance(user_id, bool) and user_id > 0
    user = db.session.get(User, user_id) if valid_id else None
    if user is None or user.role not in MANAGED_ROLES:
        # Admin accounts are reported as "not found": the API cannot touch them.
        raise NotFound("User not found.")
    return user


def faculty_teaches_student(faculty, student):
    """True if the student is actively enrolled in an active course this faculty teaches."""
    return (
        db.session.query(Enrollment.id)
        .join(Course, Course.id == Enrollment.course_id)
        .join(CourseFaculty, CourseFaculty.course_id == Course.id)
        .filter(
            CourseFaculty.faculty_id == faculty.id,
            Enrollment.student_id == student.id,
            Enrollment.is_active.is_(True),
            Course.is_active.is_(True),
        )
        .first()
        is not None
    )


def can_issue_credentials(actor, target):
    if actor is None or not actor.is_active or target is None or target.role not in MANAGED_ROLES:
        return False
    if actor.is_admin:
        return True
    if actor.is_faculty:
        return target.is_student and faculty_teaches_student(actor, target)
    return False


# ---- lookups ---------------------------------------------------------------

def _active_section(code):
    section = Section.query.filter_by(code=code).one_or_none()
    if section is None:
        raise ValidationFailed("Unknown section.", details={"section_code": [f"No section with code {code}."]})
    if not section.is_active:
        raise ValidationFailed(
            "Section is retired.", details={"section_code": [f"Section {code} is no longer active."]}
        )
    return section


def _commit_or_conflict():
    """Commit; turn a unique-constraint race into a readable 409 error."""
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        text = str(exc.orig)
        for constraint, (field, message) in _UNIQUE_FIELDS.items():
            if constraint in text:
                raise Conflict(message, details={field: [message]}) from exc
        raise


def _check_unique(username=None, email=None, exclude_id=None):
    errors = {}
    if username is not None:
        query = User.query.filter_by(username=username)
        if exclude_id:
            query = query.filter(User.id != exclude_id)
        if db.session.query(query.exists()).scalar():
            errors["username"] = [_UNIQUE_FIELDS["uq_users_username"][1]]
    if email is not None:
        query = User.query.filter_by(email=email)
        if exclude_id:
            query = query.filter(User.id != exclude_id)
        if db.session.query(query.exists()).scalar():
            errors["email"] = [_UNIQUE_FIELDS["uq_users_email"][1]]
    if errors:
        raise Conflict("Already in use.", details=errors)


def _new_user(role, data, section=None):
    temp_password = generate_temporary_password()
    user = User(
        username=data["username"],
        full_name=data["full_name"],
        email=data.get("email"),
        role=role,
        section=section,
        password_hash=hash_password(temp_password),
        must_change_password=True,
    )
    db.session.add(user)
    return user, temp_password


def _log(action, actor, target):
    # Who did what to whom. Never log the password itself.
    current_app.logger.info("%s: user %s (%s) by %s (%s)", action, target.id, target.username, actor.id, actor.username)


# ---- create ----------------------------------------------------------------

def create_faculty(actor, data):
    """data: validated FacultyCreateSchema. Returns (user, temporary_password)."""
    _require_admin(actor)
    _check_unique(username=data["username"], email=data.get("email"))
    user, temp_password = _new_user(Role.FACULTY, data)
    _commit_or_conflict()
    _log("created faculty", actor, user)
    return user, temp_password


def create_student(actor, data):
    """data: validated StudentCreateSchema. Returns (user, temporary_password)."""
    _require_admin(actor)
    section = _active_section(data["section_code"])
    _check_unique(username=data["username"], email=data.get("email"))
    user, temp_password = _new_user(Role.STUDENT, data, section)
    _commit_or_conflict()
    _log("created student", actor, user)
    return user, temp_password


def bulk_create_students(actor, rows):
    """Create many students at once. All or nothing.

    rows: validated list of StudentCreateSchema dicts. If ANY row has a problem,
    nothing is saved and every problem is reported by row number (0-based), so
    the admin can fix the whole file in one go.
    Returns a list of (user, temporary_password) in input order.
    """
    _require_admin(actor)
    errors = {}

    def add_error(index, field, message):
        errors.setdefault(str(index), {}).setdefault(field, []).append(message)

    # Duplicates inside the request itself.
    first_seen_username, first_seen_email = {}, {}
    for i, row in enumerate(rows):
        if row["username"] in first_seen_username:
            add_error(i, "username", f"Same username as row {first_seen_username[row['username']]}.")
        else:
            first_seen_username[row["username"]] = i
        email = row.get("email")
        if email:
            if email in first_seen_email:
                add_error(i, "email", f"Same email as row {first_seen_email[email]}.")
            else:
                first_seen_email[email] = i

    # Already in the database (one query each, not one per row).
    taken_usernames = {
        u for (u,) in db.session.query(User.username).filter(User.username.in_(list(first_seen_username)))
    }
    taken_emails = (
        {e for (e,) in db.session.query(User.email).filter(User.email.in_(list(first_seen_email)))}
        if first_seen_email
        else set()
    )
    codes = {row["section_code"] for row in rows}
    sections = {s.code: s for s in Section.query.filter(Section.code.in_(codes))}

    for i, row in enumerate(rows):
        if row["username"] in taken_usernames:
            add_error(i, "username", _UNIQUE_FIELDS["uq_users_username"][1])
        if row.get("email") in taken_emails:
            add_error(i, "email", _UNIQUE_FIELDS["uq_users_email"][1])
        section = sections.get(row["section_code"])
        if section is None:
            add_error(i, "section_code", f"No section with code {row['section_code']}.")
        elif not section.is_active:
            add_error(i, "section_code", f"Section {row['section_code']} is no longer active.")

    if errors:
        raise ValidationFailed(
            f"No students were created. Fix the {len(errors)} row(s) listed and send the list again.",
            details={"students": errors},
        )

    created = [_new_user(Role.STUDENT, row, sections[row["section_code"]]) for row in rows]
    _commit_or_conflict()
    current_app.logger.info("bulk created %d students by %s", len(created), actor.username)
    return created


# ---- read ------------------------------------------------------------------

def get_user(actor, user_id):
    _require_admin(actor)
    return _get_managed_user(user_id)


def _escape_like(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_users(actor, filters):
    """filters: validated UserListQuerySchema. Returns (users, total)."""
    _require_admin(actor)
    query = User.query.filter(User.role.in_(MANAGED_ROLES)).options(
        selectinload(User.section), selectinload(User.face)
    )
    if filters.get("role"):
        query = query.filter(User.role == filters["role"])
    if filters.get("section_code"):
        query = query.join(Section, Section.id == User.section_id).filter(Section.code == filters["section_code"])
    if "active" in filters:
        query = query.filter(User.is_active.is_(filters["active"]))
    if filters.get("q"):
        # Escape % and _ so a search for "_" does not match everything.
        pattern = f"%{_escape_like(filters['q'].strip())}%"
        query = query.filter(
            db.or_(User.username.ilike(pattern, escape="\\"), User.full_name.ilike(pattern, escape="\\"))
        )

    total = query.count()
    page, per_page = filters.get("page", 1), filters.get("per_page", 25)
    users = query.order_by(User.role, User.username).offset((page - 1) * per_page).limit(per_page).all()
    return users, total


# ---- update ----------------------------------------------------------------

def update_user(actor, user_id, data):
    """data: validated UserUpdateSchema (only the fields sent)."""
    _require_admin(actor)
    user = _get_managed_user(user_id)
    if not data:
        raise ValidationFailed("Nothing to update.", details={"_body": ["Send at least one field to change."]})

    if "section_code" in data:
        if not user.is_student:
            raise ValidationFailed(
                "Only students have a section.", details={"section_code": ["Faculty do not have a section."]}
            )
        user.section = _active_section(data["section_code"])

    if "email" in data and data["email"] != user.email:
        if data["email"] is not None:
            _check_unique(email=data["email"], exclude_id=user.id)
        user.email = data["email"]

    if "full_name" in data:
        user.full_name = data["full_name"]

    _commit_or_conflict()
    _log("updated", actor, user)
    return user


def set_active(actor, user_id, active):
    """Deactivate (signs the user out everywhere, blocks login) or reactivate."""
    _require_admin(actor)
    user = _get_managed_user(user_id)
    if user.is_active == active:
        return user  # already in that state; nothing to do
    user.is_active = active
    if not active:
        user.session_version += 1  # every open session ends on its next request
    db.session.commit()
    _log("activated" if active else "deactivated", actor, user)
    return user


# ---- credentials -----------------------------------------------------------

def issue_temporary_password(actor, user_id):
    """Replace the user's password with a new temporary one. Returns (user, temporary_password).

    The user is signed out everywhere and must choose a new password at next login.
    """
    if actor is None or not (actor.is_admin or actor.is_faculty):
        raise PermissionDenied("You cannot issue passwords.")
    target = _get_managed_user(user_id)

    if not can_issue_credentials(actor, target):
        if actor.is_faculty and target.is_student:
            raise PermissionDenied("You can only issue passwords to students in courses you teach.")
        raise PermissionDenied("You cannot issue a password for this account.")
    if not target.is_active:
        raise ValidationFailed(
            "This account is deactivated.", details={"_body": ["Reactivate the account before issuing a password."]}
        )

    temp_password = generate_temporary_password()
    target.password_hash = hash_password(temp_password)
    target.must_change_password = True
    target.session_version += 1
    db.session.commit()
    _log("issued temporary password", actor, target)
    return target, temp_password
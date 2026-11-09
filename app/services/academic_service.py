"""Sections, courses, faculty assignment and enrolment.

Rules
- Only the admin changes academic data. Faculty may READ the roster of a course
  they teach.
- Nothing is deleted. Sections and courses are retired (is_active = False);
  a dropped enrolment is kept with is_active = False, so history stays complete.
- A retired course is read-only: no assigning, enrolling or dropping.
- Enrolling is all or nothing: if any username is wrong, nobody is enrolled and
  every problem is listed. Already-enrolled students are simply reported.
"""
from flask import current_app
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import Course, CourseFaculty, Enrollment, Role, Section, User
from .common import commit_or_conflict, escape_like, require_admin, valid_id
from .errors import Conflict, NotFound, PermissionDenied, ValidationFailed

SECTION_UNIQUE = {"uq_sections_code": ("code", "Another section already uses this code.")}
COURSE_UNIQUE = {"uq_courses_code_term": ("code", "This course code already exists in that term.")}


def _log(message, actor, *args):
    current_app.logger.info(message + " by %s", *args, actor.username)


# ---- sections --------------------------------------------------------------

def _get_section(section_id):
    section = db.session.get(Section, section_id) if valid_id(section_id) else None
    if section is None:
        raise NotFound("Section not found.")
    return section


def _active_student_counts_by_section():
    rows = (
        db.session.query(User.section_id, func.count(User.id))
        .filter(User.role == Role.STUDENT, User.is_active.is_(True))
        .group_by(User.section_id)
    )
    return dict(rows)


def _section_code_taken(code, exclude_id=None):
    query = Section.query.filter_by(code=code)
    if exclude_id:
        query = query.filter(Section.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def create_section(actor, data):
    require_admin(actor)
    if _section_code_taken(data["code"]):
        raise Conflict("Code in use.", details={"code": [SECTION_UNIQUE["uq_sections_code"][1]]})
    section = Section(code=data["code"], name=data["name"])
    db.session.add(section)
    commit_or_conflict(SECTION_UNIQUE)
    _log("created section %s", actor, section.code)
    section.student_count = 0
    return section


def update_section(actor, section_id, data):
    require_admin(actor)
    section = _get_section(section_id)
    if not data:
        raise ValidationFailed("Nothing to update.", details={"_body": ["Send at least one field to change."]})
    if "code" in data and data["code"] != section.code:
        if _section_code_taken(data["code"], exclude_id=section.id):
            raise Conflict("Code in use.", details={"code": [SECTION_UNIQUE["uq_sections_code"][1]]})
        section.code = data["code"]
    if "name" in data:
        section.name = data["name"]
    commit_or_conflict(SECTION_UNIQUE)
    _log("updated section %s", actor, section.code)
    section.student_count = _active_student_counts_by_section().get(section.id, 0)
    return section


def set_section_active(actor, section_id, active):
    """Retire or restore a section. Retired sections cannot receive new students;
    existing students keep it."""
    require_admin(actor)
    section = _get_section(section_id)
    if section.is_active != active:
        section.is_active = active
        db.session.commit()
        _log("%s section %s", actor, "restored" if active else "retired", section.code)
    section.student_count = _active_student_counts_by_section().get(section.id, 0)
    return section


def list_sections(actor, filters):
    require_admin(actor)
    query = Section.query
    if "active" in filters:
        query = query.filter(Section.is_active.is_(filters["active"]))
    if filters.get("q"):
        pattern = f"%{escape_like(filters['q'].strip())}%"
        query = query.filter(
            db.or_(Section.code.ilike(pattern, escape="\\"), Section.name.ilike(pattern, escape="\\"))
        )
    sections = query.order_by(Section.code).all()
    counts = _active_student_counts_by_section()
    for section in sections:
        section.student_count = counts.get(section.id, 0)
    return sections


# ---- courses ---------------------------------------------------------------

def _get_course(course_id):
    course = (
        Course.query.options(selectinload(Course.faculty_links).selectinload(CourseFaculty.faculty))
        .filter_by(id=course_id)
        .one_or_none()
        if valid_id(course_id)
        else None
    )
    if course is None:
        raise NotFound("Course not found.")
    return course


def _require_active_course(course):
    if not course.is_active:
        raise ValidationFailed(
            "This course is retired.", details={"_body": ["Restore the course before changing it."]}
        )


def _active_enrolment_counts(course_ids):
    if not course_ids:
        return {}
    rows = (
        db.session.query(Enrollment.course_id, func.count(Enrollment.id))
        .filter(Enrollment.course_id.in_(course_ids), Enrollment.is_active.is_(True))
        .group_by(Enrollment.course_id)
    )
    return dict(rows)


def _with_count(course):
    course.student_count = _active_enrolment_counts([course.id]).get(course.id, 0)
    return course


def _course_taken(code, term, exclude_id=None):
    query = Course.query.filter_by(code=code, term=term)
    if exclude_id:
        query = query.filter(Course.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def create_course(actor, data):
    require_admin(actor)
    if _course_taken(data["code"], data["term"]):
        raise Conflict("Course exists.", details={"code": [COURSE_UNIQUE["uq_courses_code_term"][1]]})
    course = Course(code=data["code"], name=data["name"], term=data["term"])
    db.session.add(course)
    commit_or_conflict(COURSE_UNIQUE)
    _log("created course %s %s", actor, course.code, course.term)
    return _with_count(course)


def update_course(actor, course_id, data):
    require_admin(actor)
    course = _get_course(course_id)
    if not data:
        raise ValidationFailed("Nothing to update.", details={"_body": ["Send at least one field to change."]})
    new_code, new_term = data.get("code", course.code), data.get("term", course.term)
    if (new_code, new_term) != (course.code, course.term) and _course_taken(new_code, new_term, course.id):
        raise Conflict("Course exists.", details={"code": [COURSE_UNIQUE["uq_courses_code_term"][1]]})
    course.code, course.term = new_code, new_term
    if "name" in data:
        course.name = data["name"]
    commit_or_conflict(COURSE_UNIQUE)
    _log("updated course %s %s", actor, course.code, course.term)
    return _with_count(course)


def set_course_active(actor, course_id, active):
    """Retire or restore a course. A retired course is read-only and hidden from dashboards."""
    require_admin(actor)
    course = _get_course(course_id)
    if course.is_active != active:
        course.is_active = active
        db.session.commit()
        _log("%s course %s %s", actor, "restored" if active else "retired", course.code, course.term)
    return _with_count(course)


def get_course(actor, course_id):
    require_admin(actor)
    return _with_count(_get_course(course_id))


def list_courses(actor, filters):
    """Returns (courses, total)."""
    require_admin(actor)
    query = Course.query.options(selectinload(Course.faculty_links).selectinload(CourseFaculty.faculty))
    if filters.get("term"):
        query = query.filter(Course.term == filters["term"])
    if "active" in filters:
        query = query.filter(Course.is_active.is_(filters["active"]))
    if filters.get("faculty"):
        query = query.join(CourseFaculty, CourseFaculty.course_id == Course.id).join(
            User, User.id == CourseFaculty.faculty_id
        ).filter(User.username == filters["faculty"])
    if filters.get("q"):
        pattern = f"%{escape_like(filters['q'].strip())}%"
        query = query.filter(db.or_(Course.code.ilike(pattern, escape="\\"), Course.name.ilike(pattern, escape="\\")))

    total = query.count()
    page, per_page = filters.get("page", 1), filters.get("per_page", 25)
    courses = query.order_by(Course.term.desc(), Course.code).offset((page - 1) * per_page).limit(per_page).all()
    counts = _active_enrolment_counts([c.id for c in courses])
    for course in courses:
        course.student_count = counts.get(course.id, 0)
    return courses, total


# ---- faculty assignment ----------------------------------------------------

def _active_faculty(username):
    user = User.query.filter_by(username=username).one_or_none()
    if user is None or not user.is_faculty:
        raise ValidationFailed("Unknown faculty.", details={"username": [f"No faculty member with username {username}."]})
    if not user.is_active:
        raise ValidationFailed("Faculty deactivated.", details={"username": [f"{username} is deactivated."]})
    return user


def assign_faculty(actor, course_id, username):
    """Assign a faculty member to teach a course. Returns (course, newly_assigned: bool)."""
    require_admin(actor)
    course = _get_course(course_id)
    _require_active_course(course)
    faculty = _active_faculty(username)
    if db.session.get(CourseFaculty, (course.id, faculty.id)) is not None:
        return _with_count(course), False  # already assigned: nothing to do
    db.session.add(CourseFaculty(course=course, faculty=faculty))
    db.session.commit()
    _log("assigned %s to course %s", actor, faculty.username, course.id)
    db.session.refresh(course)
    return _with_count(course), True


def unassign_faculty(actor, course_id, username):
    require_admin(actor)
    course = _get_course(course_id)
    _require_active_course(course)
    faculty = User.query.filter_by(username=username).one_or_none()
    link = db.session.get(CourseFaculty, (course.id, faculty.id)) if faculty else None
    if link is None:
        raise NotFound(f"{username} is not assigned to this course.")
    db.session.delete(link)
    db.session.commit()
    _log("unassigned %s from course %s", actor, username, course.id)
    db.session.refresh(course)
    return _with_count(course)


# ---- enrolment -------------------------------------------------------------

def _enrol(course, students):
    """Enrol already-validated students. Returns a summary dict of usernames."""
    existing = {
        e.student_id: e
        for e in Enrollment.query.filter(
            Enrollment.course_id == course.id, Enrollment.student_id.in_([s.id for s in students])
        )
    }
    summary = {"enrolled": [], "re_enrolled": [], "already_enrolled": []}
    for student in students:
        enrollment = existing.get(student.id)
        if enrollment is None:
            db.session.add(Enrollment(course=course, student=student))
            summary["enrolled"].append(student.username)
        elif not enrollment.is_active:
            enrollment.is_active = True  # dropped earlier, back again: keep the same row
            summary["re_enrolled"].append(student.username)
        else:
            summary["already_enrolled"].append(student.username)
    db.session.commit()
    return summary


def enrol_students(actor, course_id, usernames):
    """Enrol students by username. All or nothing."""
    require_admin(actor)
    course = _get_course(course_id)
    _require_active_course(course)

    unique_names = list(dict.fromkeys(usernames))  # drop repeats, keep order
    found = {u.username: u for u in User.query.filter(User.username.in_(unique_names))}
    errors = {}
    for name in unique_names:
        user = found.get(name)
        if user is None:
            errors[name] = ["No account with this username."]
        elif not user.is_student:
            errors[name] = [f"This is a {user.role} account, not a student."]
        elif not user.is_active:
            errors[name] = ["This student account is deactivated."]
    if errors:
        raise ValidationFailed(
            f"Nobody was enrolled. Fix the {len(errors)} username(s) listed and try again.",
            details={"usernames": errors},
        )

    summary = _enrol(course, [found[name] for name in unique_names])
    _log("enrolled %d students in course %s", actor, len(summary["enrolled"]) + len(summary["re_enrolled"]), course.id)
    return summary


def enrol_section(actor, course_id, section_code):
    """Enrol every active student of a section. Deactivated students are skipped."""
    require_admin(actor)
    course = _get_course(course_id)
    _require_active_course(course)
    section = Section.query.filter_by(code=section_code).one_or_none()
    if section is None:
        raise ValidationFailed("Unknown section.", details={"section_code": [f"No section with code {section_code}."]})
    if not section.is_active:
        raise ValidationFailed("Section retired.", details={"section_code": [f"Section {section_code} is retired."]})

    students = (
        User.query.filter_by(section_id=section.id, role=Role.STUDENT, is_active=True).order_by(User.username).all()
    )
    if not students:
        raise ValidationFailed(
            "Section has no students.", details={"section_code": [f"Section {section_code} has no active students."]}
        )
    summary = _enrol(course, students)
    _log("enrolled section %s in course %s", actor, section.code, course.id)
    return summary


def drop_student(actor, course_id, username):
    """Remove a student from a course. The enrolment row is kept (inactive) for history."""
    require_admin(actor)
    course = _get_course(course_id)
    _require_active_course(course)
    enrollment = (
        Enrollment.query.join(User, User.id == Enrollment.student_id)
        .filter(Enrollment.course_id == course.id, User.username == username)
        .one_or_none()
    )
    if enrollment is None:
        raise NotFound(f"{username} is not enrolled in this course.")
    if enrollment.is_active:
        enrollment.is_active = False
        db.session.commit()
        _log("dropped %s from course %s", actor, username, course.id)
    return enrollment


def course_roster(actor, course_id, include_dropped=False):
    """Students of a course. Admin: any course. Faculty: only courses they teach."""
    course = _get_course(course_id)
    if actor is None or not actor.is_active:
        raise PermissionDenied("You cannot view this roster.")
    if not actor.is_admin:
        teaches = actor.is_faculty and any(link.faculty_id == actor.id for link in course.faculty_links)
        if not teaches:
            raise PermissionDenied("You can only view rosters of courses you teach.")

    query = (
        Enrollment.query.join(User, User.id == Enrollment.student_id)
        .options(selectinload(Enrollment.student).selectinload(User.section))
        .filter(Enrollment.course_id == course.id)
    )
    if not include_dropped:
        query = query.filter(Enrollment.is_active.is_(True))
    return course, query.order_by(User.username).all()
"""Command-line tools, run with `flask --app wsgi <command>`.

create-admin   First admin account and its API token (for Postman).
seed-demo      Demo sections, faculty, students, courses and enrolments.

These are Flask commands rather than loose scripts so they automatically
load .env, use the same config checks as the app, and can be tested.
"""
import secrets

import click
from sqlalchemy.exc import IntegrityError

from .extensions import db
from .models import Course, CourseFaculty, Enrollment, Role, Section, User
from .utils.security import generate_api_token, hash_password
from .utils.validators import is_blank, is_valid_username, normalize_username

DEMO_PASSWORD = "Demo@12345"
DEMO_TERM = "DEMO"


def register_cli(app):
    app.cli.add_command(create_admin)
    app.cli.add_command(seed_demo)


# ---- create-admin --------------------------------------------------------

@click.command("create-admin")
@click.option("--username", required=True, help="Admin login ID (letters, digits, - or _).")
@click.option("--name", "full_name", required=True, help="Admin's full name.")
@click.option("--email", default=None, help="Optional email address.")
@click.option(
    "--rotate-token",
    is_flag=True,
    help="Admin already exists: issue a new API token and invalidate the old one.",
)
def create_admin(username, full_name, email, rotate_token):
    """Create the admin account, or rotate its API token. Prints the token ONCE."""
    username = normalize_username(username)
    if not is_valid_username(username):
        raise click.ClickException(
            "Invalid username. Use 3-64 characters: lowercase letters, digits, - or _, "
            "starting with a letter or digit."
        )

    existing = User.query.filter_by(username=username).one_or_none()

    if existing is not None:
        if not existing.is_admin:
            raise click.ClickException(
                f"'{username}' already belongs to a {existing.role} account. Choose another username."
            )
        if not rotate_token:
            raise click.ClickException(
                f"Admin '{username}' already exists. Use --rotate-token to issue a new API token."
            )
        token, token_hash = generate_api_token()
        existing.api_token_hash = token_hash
        db.session.commit()
        _print_token(token, f"New API token for '{username}'. The old token no longer works.")
        return

    if rotate_token:
        raise click.ClickException(f"No admin named '{username}' exists, so there is no token to rotate.")
    if is_blank(full_name):
        raise click.ClickException("--name cannot be blank.")

    token, token_hash = generate_api_token()
    admin = User(
        username=username,
        full_name=full_name,
        email=email,
        role=Role.ADMIN,
        # Admins work through Postman with the API token. This random password
        # is never shown, so the account cannot be logged into with a password.
        password_hash=hash_password(secrets.token_urlsafe(32)),
        must_change_password=False,
        api_token_hash=token_hash,
    )
    db.session.add(admin)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        # Most likely the email is already used by another account.
        raise click.ClickException("Could not create admin: username or email already in use.") from exc

    _print_token(token, f"Admin '{username}' created.")


def _print_token(token, headline):
    click.echo(headline)
    click.echo("")
    click.echo(f"API token: {token}")
    click.echo("")
    click.echo("Store it now (e.g. as a Postman environment secret). It will NOT be shown again.")
    click.echo("Send it as the header:  Authorization: Bearer <token>")


# ---- seed-demo -----------------------------------------------------------

DEMO_SECTIONS = [
    ("DEMO-CSE-A", "Demo CSE Section A"),
    ("DEMO-CSE-B", "Demo CSE Section B"),
    ("DEMO-ECE-A", "Demo ECE Section A"),
]
DEMO_FACULTY = [
    ("demo-fac01", "Demo Faculty One"),
    ("demo-fac02", "Demo Faculty Two"),
]
# (username, name, section code)
DEMO_STUDENTS = [
    (f"demo-stu{n:02d}", f"Demo Student {n:02d}", section)
    for n, section in enumerate(
        ["DEMO-CSE-A"] * 4 + ["DEMO-CSE-B"] * 3 + ["DEMO-ECE-A"] * 3, start=1
    )
]
# (code, name, faculty usernames, sections whose students are enrolled)
DEMO_COURSES = [
    ("DS301", "Data Structures", ["demo-fac01"], ["DEMO-CSE-A", "DEMO-CSE-B"]),
    ("DB302", "Database Systems", ["demo-fac01", "demo-fac02"], ["DEMO-CSE-A"]),
    ("MA201", "Engineering Mathematics", ["demo-fac02"], ["DEMO-CSE-B", "DEMO-ECE-A"]),
]


@click.command("seed-demo")
def seed_demo():
    """Add demo data. Safe to run twice: existing demo rows are left untouched."""
    from flask import current_app

    if current_app.config.get("CONFIG_NAME") == "production":
        raise click.ClickException("seed-demo is disabled in production.")

    try:
        created = _seed_demo_rows()
    except Exception:
        # All or nothing: never leave half the demo data behind.
        db.session.rollback()
        raise

    click.echo("Demo data ready. Created: " + ", ".join(f"{v} {k}" for k, v in created.items()))
    click.echo(f"Demo login password for all demo users: {DEMO_PASSWORD} (must be changed on first login)")


def _seed_demo_rows():
    created = {"sections": 0, "faculty": 0, "students": 0, "courses": 0, "assignments": 0, "enrollments": 0}
    demo_hash = hash_password(DEMO_PASSWORD)

    sections = {}
    for code, name in DEMO_SECTIONS:
        section = Section.query.filter_by(code=code).one_or_none()
        if section is None:
            section = Section(code=code, name=name)
            db.session.add(section)
            created["sections"] += 1
        sections[code] = section
    db.session.flush()

    users = {}
    for username, name in DEMO_FACULTY:
        users[username] = _get_or_create_user(username, name, Role.FACULTY, demo_hash, None, created, "faculty")
    for username, name, section_code in DEMO_STUDENTS:
        users[username] = _get_or_create_user(
            username, name, Role.STUDENT, demo_hash, sections[section_code], created, "students"
        )
    db.session.flush()

    for code, name, faculty_names, section_codes in DEMO_COURSES:
        course = Course.query.filter_by(code=code, term=DEMO_TERM).one_or_none()
        if course is None:
            course = Course(code=code, name=name, term=DEMO_TERM)
            db.session.add(course)
            db.session.flush()
            created["courses"] += 1

        for faculty_name in faculty_names:
            faculty = users[faculty_name]
            if not faculty.is_faculty:
                raise click.ClickException(f"'{faculty_name}' exists but is not faculty; seed aborted.")
            if db.session.get(CourseFaculty, (course.id, faculty.id)) is None:
                db.session.add(CourseFaculty(course=course, faculty=faculty))
                created["assignments"] += 1

        for username, _name, section_code in DEMO_STUDENTS:
            if section_code not in section_codes:
                continue
            student = users[username]
            exists = Enrollment.query.filter_by(course_id=course.id, student_id=student.id).one_or_none()
            if exists is None:
                db.session.add(Enrollment(course=course, student=student))
                created["enrollments"] += 1

    db.session.commit()
    return created


def _get_or_create_user(username, name, role, password_hash, section, created, counter_key):
    user = User.query.filter_by(username=username).one_or_none()
    if user is not None:
        if user.role != role:
            raise click.ClickException(f"'{username}' exists with role '{user.role}', expected '{role}'; seed aborted.")
        return user
    user = User(
        username=username,
        full_name=name,
        role=role,
        password_hash=password_hash,
        section=section,
        must_change_password=True,
    )
    db.session.add(user)
    created[counter_key] += 1
    return user
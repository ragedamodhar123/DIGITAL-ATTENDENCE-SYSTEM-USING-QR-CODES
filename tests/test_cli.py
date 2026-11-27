"""Module 0, part 4: create-admin and seed-demo commands."""
import re

from app.extensions import db
from app.models import Course, CourseFaculty, Enrollment, Role, Section, User
from app.utils.security import hash_api_token, verify_password
from tests.factories import make_user

TOKEN_LINE = re.compile(r"API token: (\S+)")


def _token_from(output):
    match = TOKEN_LINE.search(output)
    assert match, output
    return match.group(1)


def _admin(username):
    db.session.expire_all()  # drop cached objects so we read what the command saved
    return User.query.filter_by(username=username).one_or_none()


# ---- create-admin --------------------------------------------------------

def test_create_admin_prints_working_token_once(cli):
    result = cli.invoke(args=["create-admin", "--username", " Admin1 ", "--name", "College Admin"])
    assert result.exit_code == 0, result.output
    token = _token_from(result.output)
    assert result.output.count(token) == 1

    admin = _admin("admin1")
    assert admin.role == Role.ADMIN
    assert admin.api_token_hash == hash_api_token(token)
    assert token not in admin.api_token_hash
    assert admin.must_change_password is False


def test_admin_password_is_random_and_unusable(cli):
    cli.invoke(args=["create-admin", "--username", "admin1", "--name", "Admin"])
    admin = _admin("admin1")
    for guess in ["admin", "admin1", "password", "Admin@123", ""]:
        assert verify_password(admin.password_hash, guess) is False


def test_invalid_username_rejected(cli):
    for bad in ["ad", "has space", "UPPER!", "-starts-with-dash", "x" * 65]:
        result = cli.invoke(args=["create-admin", "--username", bad, "--name", "Admin"])
        assert result.exit_code != 0, bad
        assert "Invalid username" in result.output
    assert User.query.count() == 0


def test_blank_name_rejected(cli):
    result = cli.invoke(args=["create-admin", "--username", "admin1", "--name", "   "])
    assert result.exit_code != 0
    assert _admin("admin1") is None


def test_existing_admin_needs_rotate_flag(cli):
    cli.invoke(args=["create-admin", "--username", "admin1", "--name", "Admin"])
    result = cli.invoke(args=["create-admin", "--username", "admin1", "--name", "Admin"])
    assert result.exit_code != 0
    assert "--rotate-token" in result.output


def test_rotate_token_invalidates_old_token(cli):
    first = _token_from(cli.invoke(args=["create-admin", "--username", "admin1", "--name", "Admin"]).output)
    result = cli.invoke(args=["create-admin", "--username", "admin1", "--name", "Admin", "--rotate-token"])
    assert result.exit_code == 0, result.output
    second = _token_from(result.output)

    admin = _admin("admin1")
    assert first != second
    assert admin.api_token_hash == hash_api_token(second)
    assert admin.api_token_hash != hash_api_token(first)


def test_rotate_for_missing_admin_fails(cli):
    result = cli.invoke(args=["create-admin", "--username", "ghost", "--name", "X", "--rotate-token"])
    assert result.exit_code != 0
    assert "No admin" in result.output


def test_cannot_take_over_existing_faculty_username(cli):
    faculty = make_user(role=Role.FACULTY, username="fac001")
    db.session.commit()
    result = cli.invoke(args=["create-admin", "--username", "fac001", "--name", "Admin", "--rotate-token"])
    assert result.exit_code != 0
    assert "faculty" in result.output
    db.session.expire_all()
    assert faculty.role == Role.FACULTY and faculty.api_token_hash is None


def test_duplicate_email_gives_clean_error(cli):
    make_user(email="admin@college.edu")
    db.session.commit()
    result = cli.invoke(
        args=["create-admin", "--username", "admin1", "--name", "Admin", "--email", "ADMIN@college.edu"]
    )
    assert result.exit_code != 0
    assert "already in use" in result.output
    assert "Traceback" not in result.output


# ---- seed-demo -----------------------------------------------------------

def test_seed_demo_creates_linked_data(cli):
    result = cli.invoke(args=["seed-demo"])
    assert result.exit_code == 0, result.output
    db.session.expire_all()

    assert Section.query.count() == 3
    assert User.query.filter_by(role=Role.FACULTY).count() == 2
    assert User.query.filter_by(role=Role.STUDENT).count() == 10
    assert Course.query.filter_by(term="DEMO").count() == 3
    assert CourseFaculty.query.count() == 4

    # DS301 has students from two sections (many-to-many across sections).
    ds301 = Course.query.filter_by(code="DS301").one()
    sections = {e.student.section.code for e in ds301.enrollments}
    assert sections == {"DEMO-CSE-A", "DEMO-CSE-B"}

    student = User.query.filter_by(username="demo-stu01").one()
    assert student.must_change_password is True
    assert verify_password(student.password_hash, "Demo@12345")


def test_seed_demo_is_safe_to_run_twice(cli):
    cli.invoke(args=["seed-demo"])
    counts = (User.query.count(), Enrollment.query.count(), CourseFaculty.query.count())
    result = cli.invoke(args=["seed-demo"])
    assert result.exit_code == 0, result.output
    db.session.expire_all()
    assert (User.query.count(), Enrollment.query.count(), CourseFaculty.query.count()) == counts
    assert "0 students" in result.output


def test_seed_demo_does_not_reset_changed_passwords(cli):
    cli.invoke(args=["seed-demo"])
    student = User.query.filter_by(username="demo-stu01").one()
    student.password_hash = "changed-by-student"
    db.session.commit()
    cli.invoke(args=["seed-demo"])
    db.session.expire_all()
    assert User.query.filter_by(username="demo-stu01").one().password_hash == "changed-by-student"


def test_seed_demo_aborts_on_role_clash(cli):
    make_user(role=Role.ADMIN, username="demo-fac01")
    db.session.commit()
    result = cli.invoke(args=["seed-demo"])
    assert result.exit_code != 0
    assert "demo-fac01" in result.output
    db.session.expire_all()
    assert Section.query.count() == 0  # nothing half-created


def test_seed_demo_refused_in_production(app, cli):
    app.config["CONFIG_NAME"] = "production"
    try:
        result = cli.invoke(args=["seed-demo"])
    finally:
        app.config["CONFIG_NAME"] = "testing"
    assert result.exit_code != 0
    assert "production" in result.output
    assert User.query.count() == 0
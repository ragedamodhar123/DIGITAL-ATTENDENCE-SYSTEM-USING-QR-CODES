"""Small helpers that create valid rows, so each test only states what matters to it."""
from itertools import count

from app.extensions import db
from app.models import Course, CourseFaculty, Enrollment, Role, Section, User
from app.utils.security import hash_password

_seq = count(1)
# Hashing is deliberately slow; hash once and reuse for every test user.
TEST_PASSWORD = "Correct-Horse-9"
_TEST_HASH = hash_password(TEST_PASSWORD)


def make_section(code=None, name="Test Section"):
    section = Section(code=code or f"SEC-{next(_seq)}", name=name)
    db.session.add(section)
    db.session.flush()
    return section


def make_user(role=Role.FACULTY, username=None, section=None, **extra):
    if role == Role.STUDENT and section is None:
        section = make_section()
    user = User(
        username=username or f"{role}{next(_seq):04d}",
        full_name=extra.pop("full_name", f"Test {role.title()}"),
        role=role,
        password_hash=_TEST_HASH,
        section=section,
        **extra,
    )
    db.session.add(user)
    db.session.flush()
    return user


def make_course(code=None, term="2026-ODD", name="Test Course"):
    course = Course(code=code or f"CS{next(_seq):03d}", term=term, name=name)
    db.session.add(course)
    db.session.flush()
    return course


def assign_faculty(course, faculty):
    link = CourseFaculty(course=course, faculty=faculty)
    db.session.add(link)
    db.session.flush()
    return link


def enroll(course, student):
    enrollment = Enrollment(course=course, student=student)
    db.session.add(enrollment)
    db.session.flush()
    return enrollment


# ---- Attendance (Part 3) ------------------------------------------------

def make_session(course=None, created_by=None, kind="live", status="open", session_date=None, **extra):
    from datetime import date, datetime, timedelta, timezone

    from app.models import AttendanceSession

    now = datetime.now(timezone.utc)
    values = {
        "course": course or make_course(),
        "created_by": created_by or make_user(),
        "kind": kind,
        "status": status,
        "session_date": session_date or date.today(),
        "started_at": now,
    }
    if kind == "live":
        values["qr_secret"] = "ab" * 32
        values["expires_at"] = now + timedelta(hours=2)
    if status == "closed":
        values["ended_at"] = now + timedelta(minutes=50 if kind == "live" else 0)
    values.update(extra)
    session = AttendanceSession(**values)
    db.session.add(session)
    db.session.flush()
    return session


def make_record(session=None, student=None, status="present", method="qr_face", **extra):
    from app.models import AttendanceRecord

    values = {
        "session": session or make_session(),
        "student": student or make_user(role=Role.STUDENT),
        "status": status,
        "method": method,
    }
    if method == "qr_face":
        values.setdefault("face_distance", 0.25)
    values.update(extra)
    record = AttendanceRecord(**values)
    db.session.add(record)
    db.session.flush()
    return record
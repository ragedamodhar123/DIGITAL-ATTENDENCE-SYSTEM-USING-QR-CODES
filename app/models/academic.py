"""Sections, courses, faculty assignments and enrolments.

Deletion rule everywhere: RESTRICT. The database never silently deletes
linked rows. To retire something, set is_active = False.

passive_deletes="all" on parent relationships stops SQLAlchemy from trying
to blank out child foreign keys before a delete, so PostgreSQL's RESTRICT
rule is always the thing that refuses.
"""
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, func, true
from sqlalchemy.orm import validates

from ..extensions import db
from .base import TimestampMixin, clean_str


def _upper(value):
    value = clean_str(value)
    return value.upper() if isinstance(value, str) else value


class Section(TimestampMixin, db.Model):
    """A student's home class, e.g. CSE-A-2024."""

    __tablename__ = "sections"
    __table_args__ = (
        CheckConstraint("code = upper(code)", name="code_uppercase"),
        CheckConstraint("length(code) > 0", name="code_not_blank"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
    )

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), nullable=False, unique=True)
    name = db.Column(db.String(120), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=true())

    students = db.relationship("User", back_populates="section", passive_deletes="all")

    @validates("code")
    def _normalize_code(self, _key, value):
        return _upper(value)

    @validates("name")
    def _normalize_name(self, _key, value):
        return clean_str(value)

    def __repr__(self):
        return f"<Section {self.code}>"


class Course(TimestampMixin, db.Model):
    """A course offered in one academic term. CS301 next year is a new row."""

    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("code", "term"),
        CheckConstraint("code = upper(code)", name="code_uppercase"),
        CheckConstraint("term = upper(term)", name="term_uppercase"),
        CheckConstraint("length(code) > 0", name="code_not_blank"),
        CheckConstraint("length(term) > 0", name="term_not_blank"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
    )

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    term = db.Column(db.String(16), nullable=False)  # e.g. 2026-ODD
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=true())

    faculty_links = db.relationship("CourseFaculty", back_populates="course", passive_deletes="all")
    enrollments = db.relationship("Enrollment", back_populates="course", passive_deletes="all")

    @validates("code", "term")
    def _normalize_upper(self, _key, value):
        return _upper(value)

    @validates("name")
    def _normalize_name(self, _key, value):
        return clean_str(value)

    def __repr__(self):
        return f"<Course {self.code} {self.term}>"


class CourseFaculty(db.Model):
    """Which faculty teach which course (many-to-many)."""

    __tablename__ = "course_faculty"
    __table_args__ = (
        # (faculty_id, faculty_role) must match a real (users.id, users.role),
        # and faculty_role can only be 'faculty': a student can never be assigned.
        ForeignKeyConstraint(
            ["faculty_id", "faculty_role"], ["users.id", "users.role"], ondelete="RESTRICT"
        ),
        CheckConstraint("faculty_role = 'faculty'", name="faculty_role_only"),
    )

    course_id = db.Column(
        db.Integer, db.ForeignKey("courses.id", ondelete="RESTRICT"), primary_key=True
    )
    faculty_id = db.Column(db.Integer, primary_key=True, index=True)
    faculty_role = db.Column(db.String(16), nullable=False, default="faculty", server_default="faculty")
    assigned_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())

    course = db.relationship("Course", back_populates="faculty_links")
    faculty = db.relationship("User", back_populates="teaching_links")

    def __repr__(self):
        return f"<CourseFaculty course={self.course_id} faculty={self.faculty_id}>"


class Enrollment(TimestampMixin, db.Model):
    """Which students take which course (many-to-many, across sections)."""

    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("course_id", "student_id"),
        ForeignKeyConstraint(
            ["student_id", "student_role"], ["users.id", "users.role"], ondelete="RESTRICT"
        ),
        CheckConstraint("student_role = 'student'", name="student_role_only"),
    )

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(
        db.Integer, db.ForeignKey("courses.id", ondelete="RESTRICT"), nullable=False
    )
    student_id = db.Column(db.Integer, nullable=False, index=True)
    student_role = db.Column(db.String(16), nullable=False, default="student", server_default="student")
    # Dropping a course sets this False instead of deleting, so reports still know
    # the student was once enrolled. Re-enrolling flips it back.
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=true())

    course = db.relationship("Course", back_populates="enrollments")
    student = db.relationship("User", back_populates="enrollments")

    def __repr__(self):
        return f"<Enrollment course={self.course_id} student={self.student_id}>"

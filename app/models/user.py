"""User accounts for all three roles."""
from flask_login import UserMixin
from sqlalchemy import CheckConstraint, UniqueConstraint, true
from sqlalchemy.orm import validates

from ..extensions import db, login_manager
from .base import TimestampMixin, clean_str


class Role:
    ADMIN = "admin"
    FACULTY = "faculty"
    STUDENT = "student"
    ALL = (ADMIN, FACULTY, STUDENT)


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        # Lets other tables point at (id, role), so the DATABASE guarantees
        # that only faculty can teach and only students can enrol.
        UniqueConstraint("id", "role"),
        CheckConstraint("role IN ('admin', 'faculty', 'student')", name="valid_role"),
        CheckConstraint("username = lower(username)", name="username_lowercase"),
        CheckConstraint("length(username) >= 3", name="username_min_length"),
        CheckConstraint("length(trim(full_name)) > 0", name="full_name_not_blank"),
        CheckConstraint("email IS NULL OR email = lower(email)", name="email_lowercase"),
        CheckConstraint("(role = 'student') = (section_id IS NOT NULL)", name="student_has_section"),
        CheckConstraint("api_token_hash IS NULL OR role = 'admin'", name="api_token_admin_only"),
        CheckConstraint("session_version >= 1", name="session_version_positive"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # Login ID: roll number for students, employee ID for faculty. Stored lowercase.
    username = db.Column(db.String(64), nullable=False, unique=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), unique=True)  # optional; many NULLs allowed
    role = db.Column(db.String(16), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    must_change_password = db.Column(db.Boolean, nullable=False, default=True, server_default=true())
    # Deactivate instead of deleting: attendance history must keep pointing at real users.
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=true())
    # Bumped on password change / forced logout; old cookies stop working instantly.
    session_version = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    api_token_hash = db.Column(db.String(64), unique=True)
    # Home class/section. Required for students, must be empty for everyone else.
    section_id = db.Column(db.Integer, db.ForeignKey("sections.id", ondelete="RESTRICT"), index=True)
    last_login_at = db.Column(db.DateTime(timezone=True))
    password_changed_at = db.Column(db.DateTime(timezone=True))

    section = db.relationship("Section", back_populates="students")
    # passive_deletes="all": never let the ORM blank out child rows on delete;
    # PostgreSQL's RESTRICT rule decides (and refuses).
    teaching_links = db.relationship("CourseFaculty", back_populates="faculty", passive_deletes="all")
    enrollments = db.relationship("Enrollment", back_populates="student", passive_deletes="all")
    face = db.relationship("FaceEmbedding", back_populates="student", uselist=False, passive_deletes="all")

    @validates("username", "role")
    def _normalize_lower(self, _key, value):
        value = clean_str(value)
        return value.lower() if isinstance(value, str) else value

    @validates("email")
    def _normalize_email(self, _key, value):
        value = clean_str(value)
        if value == "":
            return None
        return value.lower() if isinstance(value, str) else value

    @validates("full_name")
    def _normalize_name(self, _key, value):
        return clean_str(value)

    @property
    def is_admin(self):
        return self.role == Role.ADMIN

    @property
    def is_faculty(self):
        return self.role == Role.FACULTY

    @property
    def is_student(self):
        return self.role == Role.STUDENT

    def get_id(self):
        """Flask-Login stores this in the cookie. Including session_version means
        changing it logs the user out on every device."""
        return f"{self.id}:{self.session_version}"

    def __repr__(self):
        return f"<User {self.id} {self.username} ({self.role})>"


@login_manager.user_loader
def load_user(raw_id):
    """Turn the cookie value back into a User, or None (= logged out)."""
    try:
        id_part, version_part = str(raw_id).split(":", 1)
        user_id, version = int(id_part), int(version_part)
    except (TypeError, ValueError):
        return None

    user = db.session.get(User, user_id)
    if user is None or not user.is_active or user.session_version != version:
        return None
    return user
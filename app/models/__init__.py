"""All models, imported in one place so Alembic and create_all() see every table."""
from .academic import Course, CourseFaculty, Enrollment, Section
from .attendance import (
    AttendanceAudit,
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    MarkMethod,
    PendingCheckin,
    ScanLog,
    SessionKind,
    SessionStatus,
)
from .face import FaceEmbedding
from .user import Role, User, load_user

__all__ = [
    "AttendanceAudit",
    "AttendanceRecord",
    "AttendanceSession",
    "AttendanceStatus",
    "Course",
    "CourseFaculty",
    "Enrollment",
    "FaceEmbedding",
    "MarkMethod",
    "PendingCheckin",
    "Role",
    "ScanLog",
    "Section",
    "SessionKind",
    "SessionStatus",
    "User",
    "load_user",
]
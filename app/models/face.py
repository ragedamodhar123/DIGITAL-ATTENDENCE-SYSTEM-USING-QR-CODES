"""Enrolled face data: one embedding per student, no photos stored."""
from sqlalchemy import CheckConstraint, ForeignKeyConstraint

from ..extensions import db
from .base import TimestampMixin

FLOAT32_BYTES = 4


class FaceEmbedding(TimestampMixin, db.Model):
    """A list of numbers describing a student's face, stored as raw float32 bytes.

    model_name is stored because numbers from two different face models cannot
    be compared. If the model is ever changed, old rows are detected and the
    student is asked to re-enrol.
    """

    __tablename__ = "face_embeddings"
    __table_args__ = (
        ForeignKeyConstraint(["student_id", "student_role"], ["users.id", "users.role"], ondelete="RESTRICT"),
        CheckConstraint("student_role = 'student'", name="student_role_only"),
        CheckConstraint("dim BETWEEN 64 AND 2048", name="dim_range"),
        # The stored bytes must be exactly `dim` float32 values; a truncated
        # or corrupted upload can never be saved.
        CheckConstraint(f"octet_length(embedding) = dim * {FLOAT32_BYTES}", name="embedding_size_matches_dim"),
        CheckConstraint("length(trim(model_name)) > 0", name="model_name_not_blank"),
        CheckConstraint("quality IS NULL OR (quality >= 0 AND quality <= 1)", name="quality_range"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # unique=True: one enrolled face per student. Re-enrolment replaces it.
    student_id = db.Column(db.Integer, nullable=False, unique=True)
    student_role = db.Column(db.String(16), nullable=False, default="student", server_default="student")
    model_name = db.Column(db.String(64), nullable=False)
    dim = db.Column(db.Integer, nullable=False)
    embedding = db.Column(db.LargeBinary, nullable=False)
    quality = db.Column(db.Float)  # 0..1 score from the detector, for diagnosing bad enrolments

    student = db.relationship("User", back_populates="face")

    def __repr__(self):
        return f"<FaceEmbedding student={self.student_id} model={self.model_name} dim={self.dim}>"
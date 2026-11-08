"""Module 0, part 3: face embeddings."""
import struct

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import FaceEmbedding, Role
from tests.factories import make_user


def _vector_bytes(dim):
    return struct.pack(f"<{dim}f", *([0.1] * dim))


def _expect_db_rejects(fragment=None):
    with pytest.raises(IntegrityError) as exc:
        db.session.flush()
    db.session.rollback()
    if fragment:
        assert fragment in str(exc.value)


def _face(student, dim=128, data=None, **extra):
    values = {
        "student": student,
        "model_name": "sface-2021dec",
        "dim": dim,
        "embedding": data if data is not None else _vector_bytes(dim),
    }
    values.update(extra)
    row = FaceEmbedding(**values)
    db.session.add(row)
    return row


def test_valid_embedding_saved_and_read_back():
    student = make_user(role=Role.STUDENT)
    row = _face(student, quality=0.92)
    db.session.flush()
    db.session.refresh(row)
    assert len(row.embedding) == 128 * 4
    assert struct.unpack("<128f", row.embedding)[0] == pytest.approx(0.1)


def test_one_face_per_student():
    student = make_user(role=Role.STUDENT)
    _face(student)
    db.session.flush()
    _face(student)
    _expect_db_rejects("uq_face_embeddings_student_id")


@pytest.mark.parametrize("role", [Role.FACULTY, Role.ADMIN])
def test_only_students_have_faces(role):
    _face(make_user(role=role))
    _expect_db_rejects()


def test_truncated_embedding_rejected():
    _face(make_user(role=Role.STUDENT), dim=128, data=_vector_bytes(127))
    _expect_db_rejects("embedding_size_matches_dim")


@pytest.mark.parametrize("dim", [0, 32, 4096])
def test_dim_out_of_range_rejected(dim):
    _face(make_user(role=Role.STUDENT), dim=dim, data=_vector_bytes(dim))
    _expect_db_rejects("dim_range")


@pytest.mark.parametrize("quality", [-0.1, 1.5])
def test_quality_out_of_range_rejected(quality):
    _face(make_user(role=Role.STUDENT), quality=quality)
    _expect_db_rejects("quality_range")


def test_blank_model_name_rejected():
    _face(make_user(role=Role.STUDENT), model_name="  ")
    _expect_db_rejects("model_name_not_blank")
"""Module 3, part 1: face rules (decoding, quality, registration, verification).

Uses a stand-in engine, so these run without the model files. Real-model
checks live in test_face_models.py.
"""
import base64
import io

import numpy as np
import pytest
from PIL import Image

from app.extensions import db
from app.models import FaceEmbedding, Role
from app.services import face_service as fs
from app.services.errors import Conflict, PermissionDenied, ValidationFailed
from app.services.face_engine import EMBEDDING_DIM, MODEL_NAME, missing_or_damaged_models
from tests.face_helpers import FakeEngine, encode, face, near, photo, unit
from tests.factories import make_user


def reason_of(call):
    with pytest.raises(fs.FaceProblem) as exc:
        call()
    return exc.value.reason


# ---- decoding --------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_common_formats_decode(fmt):
    image = fs.decode_image(encode(photo(), fmt))
    assert image.shape == (480, 640, 3)


def test_large_photo_is_scaled_down_for_speed():
    image = fs.decode_image(encode(photo(width=2560, height=1920)))
    assert max(image.shape[:2]) == 1280


@pytest.mark.parametrize("data", [b"", b"not an image at all", None, "text"])
def test_unreadable_input(data):
    assert reason_of(lambda: fs.decode_image(data)) == "unreadable_image"


def test_gif_not_accepted():
    assert reason_of(lambda: fs.decode_image(encode(photo(), "GIF"))) == "unreadable_image"


def test_byte_size_limit(app, monkeypatch):
    monkeypatch.setitem(app.config, "FACE_MAX_IMAGE_BYTES", 1000)
    assert reason_of(lambda: fs.decode_image(encode(photo()))) == "image_too_large"


def test_huge_dimensions_rejected_before_decoding():
    # 5000x200 compresses to a tiny file but would be a large image in memory.
    tiny_file = encode(np.zeros((200, 5000, 3), dtype=np.uint8), "PNG")
    assert len(tiny_file) < 50_000
    assert reason_of(lambda: fs.decode_image(tiny_file)) == "image_too_large"


def test_tiny_image_rejected():
    assert reason_of(lambda: fs.decode_image(encode(photo(width=100, height=100)))) == "image_too_small"


def test_phone_rotation_is_applied():
    # Phones often save landscape pixels plus an "orientation: rotate 90" note.
    exif = Image.Exif()
    exif[0x0112] = 6
    data = encode(photo(width=640, height=480), "JPEG", exif=exif.tobytes())
    assert fs.decode_image(data).shape[:2] == (640, 480)


def test_data_url_decoding():
    raw = encode(photo())
    url = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
    assert fs.decode_data_url(url) == raw


@pytest.mark.parametrize(
    "url",
    [None, "", "image/jpeg;base64,abc", "data:text/html;base64,PGh0bWw+", "data:image/jpeg,notbase64", "data:image/jpeg;base64,***"],
)
def test_bad_data_urls(url):
    assert reason_of(lambda: fs.decode_data_url(url)) == "unreadable_image"


# ---- analysis --------------------------------------------------------------

def test_good_photo_gives_a_sample():
    sample = fs.analyse(photo(), FakeEngine())
    assert sample.embedding.shape == (EMBEDDING_DIM,)
    assert sample.sharpness > 25 and 40 < sample.brightness < 225


@pytest.mark.parametrize(
    "detections,reason",
    [
        ([], "no_face"),
        ([face(score=0.5)], "no_face"),
        ([face(x=50), face(x=330, size=180)], "multiple_faces"),
        ([face(size=60)], "face_too_small"),
        ([face(x=500, size=200)], "face_cut_off"),
        ([face(x=-30)], "face_cut_off"),
    ],
)
def test_face_problems(detections, reason):
    assert reason_of(lambda: fs.analyse(photo(), FakeEngine(detections))) == reason


def test_small_background_face_is_ignored():
    # A classmate far behind: much smaller than the main face.
    engine = FakeEngine([face(x=200, size=220), face(x=20, y=20, size=60)])
    fs.analyse(photo(), engine)


def test_main_face_is_the_largest_even_if_listed_second():
    engine = FakeEngine([face(x=20, y=20, size=60), face(x=200, size=220)])
    fs.analyse(photo(), engine)


@pytest.mark.parametrize("kwargs,reason", [({"brightness": 15}, "too_dark"), ({"brightness": 245}, "too_bright"), ({"blur": 15}, "too_blurry")])
def test_quality_problems(kwargs, reason):
    assert reason_of(lambda: fs.analyse(photo(**kwargs), FakeEngine())) == reason


def test_problem_messages_tell_the_student_what_to_do():
    problem = fs.FaceProblem("face_too_small")
    assert "Move closer" in problem.message
    assert problem.to_dict()["reason"] == "face_too_small"
    assert problem.status_code == 422


# ---- registration ----------------------------------------------------------

def _captures(n=3):
    return [encode(photo(seed=i)) for i in range(n)]


def _student(**extra):
    return make_user(role=Role.STUDENT, must_change_password=False, **extra)


def test_enrol_stores_average_of_the_captures():
    student = _student()
    base = unit(7)
    vectors = [near(base, seed=s) for s in (1, 2, 3)]
    record = fs.enrol(student, student, _captures(), FakeEngine(embeddings=vectors))

    stored = fs.from_bytes(record.embedding)
    expected = np.mean(vectors, axis=0)
    expected /= np.linalg.norm(expected)
    assert np.allclose(stored, expected, atol=1e-6)
    assert np.isclose(np.linalg.norm(stored), 1.0, atol=1e-5)
    assert (record.model_name, record.dim) == (MODEL_NAME, EMBEDDING_DIM)
    assert 0 <= record.quality <= 1
    assert student.face == record


def test_nobody_registers_someone_elses_face():
    student, other = _student(), _student()
    faculty = make_user(role=Role.FACULTY)
    for actor in (other, faculty, None):
        with pytest.raises(PermissionDenied):
            fs.enrol(actor, student, _captures(), FakeEngine())


def test_already_registered_face_cannot_be_replaced_by_student():
    student = _student()
    fs.enrol(student, student, _captures(), FakeEngine(embeddings=[near(unit(1), seed=s) for s in (1, 2, 3)]))
    with pytest.raises(Conflict) as exc:
        fs.enrol(student, student, _captures(), FakeEngine())
    assert "ask your faculty" in exc.value.details["_body"][0]


@pytest.mark.parametrize("images", [[], [b"a"], "not a list", None])
def test_wrong_number_of_photos(images):
    student = _student()
    with pytest.raises(ValidationFailed) as exc:
        fs.enrol(student, student, images, FakeEngine())
    assert "images" in exc.value.details


def test_same_photo_three_times_rejected():
    student = _student()
    same = encode(photo())
    with pytest.raises(ValidationFailed) as exc:
        fs.enrol(student, student, [same, same, same], FakeEngine())
    assert "new capture" in exc.value.details["images"][0]


def test_bad_photo_reported_by_position_and_nothing_saved():
    student = _student()
    images = [encode(photo(seed=1)), encode(photo(seed=2, brightness=10)), encode(photo(seed=3))]
    with pytest.raises(ValidationFailed) as exc:
        fs.enrol(student, student, images, FakeEngine())
    assert set(exc.value.details["images"]) == {"1"}
    assert "too dark" in exc.value.details["images"]["1"][0]
    assert FaceEmbedding.query.count() == 0


def test_photos_of_different_people_rejected():
    student = _student()
    vectors = [unit(1), unit(2), unit(3)]  # unrelated faces
    with pytest.raises(ValidationFailed) as exc:
        fs.enrol(student, student, _captures(), FakeEngine(embeddings=vectors))
    assert "same person" in exc.value.details["images"][0]
    assert FaceEmbedding.query.count() == 0


def test_deactivated_student_cannot_register():
    student = _student(is_active=False)
    with pytest.raises(PermissionDenied):
        fs.enrol(student, student, _captures(), FakeEngine())


# ---- verification ----------------------------------------------------------

def _registered(seed=5):
    student = _student()
    base = unit(seed)
    fs.enrol(student, student, _captures(), FakeEngine(embeddings=[near(base, seed=s) for s in (1, 2, 3)]))
    return student, base


def test_same_person_matches():
    student, base = _registered()
    result = fs.verify(student, encode(photo(seed=9)), FakeEngine(embeddings=[near(base, seed=8)]))
    assert result.matched is True
    assert result.similarity > 0.7 and np.isclose(result.distance, 1 - result.similarity)


def test_different_person_does_not_match():
    student, _ = _registered()
    result = fs.verify(student, encode(photo(seed=9)), FakeEngine(embeddings=[unit(99)]))
    assert result.matched is False and result.distance > 0.5


def test_threshold_is_respected(app, monkeypatch):
    student, base = _registered()
    probe = near(base, noise=0.08, seed=4)
    score = float(np.dot(probe, fs.from_bytes(student.face.embedding)))
    monkeypatch.setitem(app.config, "FACE_MATCH_THRESHOLD", score + 0.01)
    assert fs.verify(student, encode(photo(seed=9)), FakeEngine(embeddings=[probe])).matched is False
    monkeypatch.setitem(app.config, "FACE_MATCH_THRESHOLD", score - 0.01)
    assert fs.verify(student, encode(photo(seed=9)), FakeEngine(embeddings=[probe])).matched is True


def test_verify_without_registration():
    with pytest.raises(fs.FaceNotRegistered):
        fs.verify(_student(), encode(photo()), FakeEngine())


def test_registration_from_another_model_needs_redoing():
    student, _ = _registered()
    student.face.model_name = "some-older-model"
    db.session.flush()
    with pytest.raises(fs.FaceNotRegistered) as exc:
        fs.verify(student, encode(photo()), FakeEngine())
    assert "registered again" in exc.value.message


def test_verify_reports_photo_problems():
    student, _ = _registered()
    assert reason_of(lambda: fs.verify(student, encode(photo()), FakeEngine(detections=[]))) == "no_face"


# ---- models and engine -----------------------------------------------------

def test_missing_models_give_a_clear_503(app, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "FACE_MODEL_DIR", str(tmp_path))
    monkeypatch.delitem(app.extensions, "face_engine", raising=False)
    with pytest.raises(fs.FaceServiceUnavailable) as exc:
        fs.get_engine()
    assert exc.value.status_code == 503
    assert "not available" in exc.value.message


def test_damaged_model_files_detected(tmp_path):
    from app.services.face_engine import MODEL_FILES

    for name in MODEL_FILES:
        (tmp_path / name).write_bytes(b"not really a model")
    assert sorted(missing_or_damaged_models(str(tmp_path))) == sorted(MODEL_FILES)


def test_embedding_round_trip():
    vector = unit(3)
    assert np.array_equal(fs.from_bytes(fs.to_bytes(vector)), vector)
    assert len(fs.to_bytes(vector)) == EMBEDDING_DIM * 4
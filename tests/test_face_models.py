"""Module 3, part 1: the face rules with the REAL OpenCV models.

Skipped automatically until the models are downloaded:
    flask --app wsgi download-face-models
Then run:  pytest -v tests/test_face_models.py
"""
import os

import cv2
import numpy as np
import pytest

from app.models import Role
from app.services import face_service as fs
from app.services.face_engine import EMBEDDING_DIM, OpenCVFaceEngine, missing_or_damaged_models
from tests.face_helpers import encode
from tests.factories import make_user

SAMPLE = os.path.join(os.path.dirname(__file__), "fixtures", "face_sample.jpg")


@pytest.fixture(scope="module")
def engine(app):
    model_dir = app.config["FACE_MODEL_DIR"]
    if missing_or_damaged_models(model_dir):
        pytest.skip("Face models not downloaded. Run: flask --app wsgi download-face-models")
    return OpenCVFaceEngine(model_dir, app.config["FACE_MIN_DETECTION_SCORE"])


@pytest.fixture(scope="module")
def sample():
    image = cv2.imread(SAMPLE)
    assert image is not None, "tests/fixtures/face_sample.jpg is missing"
    return image


def _shift(image, dx, dy):
    matrix = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(image, matrix, (image.shape[1], image.shape[0]), borderMode=cv2.BORDER_REPLICATE)


def test_finds_exactly_one_face_in_the_sample(engine, sample):
    faces = [d for d in engine.detect(sample) if d.score >= 0.85]
    assert len(faces) == 1
    assert faces[0].width > 60


def test_embedding_is_128_numbers_of_length_one(engine, sample):
    face = engine.detect(sample)[0]
    vector = engine.embed(sample, face)
    assert vector.shape == (EMBEDDING_DIM,)
    assert np.isclose(np.linalg.norm(vector), 1.0, atol=1e-5)


def test_sample_photo_passes_quality_checks(engine, sample):
    result = fs.analyse(sample, engine)
    print(f"\nsample: sharpness={result.sharpness:.1f} brightness={result.brightness:.1f} score={result.detection_score:.2f}")


def test_register_then_verify_same_person(engine, sample):
    student = make_user(role=Role.STUDENT, must_change_password=False)
    captures = [
        encode(sample),
        encode(cv2.convertScaleAbs(sample, alpha=1.0, beta=15)),  # a bit brighter
        encode(_shift(sample, 8, -6)),                             # slightly moved
    ]
    fs.enrol(student, student, captures, engine)

    result = fs.verify(student, encode(cv2.convertScaleAbs(sample, alpha=0.9, beta=0)), engine)
    print(f"\nsame person similarity: {result.similarity:.3f} (threshold 0.363)")
    assert result.matched is True


def test_blank_image_has_no_face(engine):
    blank = np.full((480, 640, 3), 128, dtype=np.uint8)
    with pytest.raises(fs.FaceProblem) as exc:
        fs.analyse(blank, engine)
    assert exc.value.reason == "no_face"


def test_two_people_side_by_side_rejected(engine, sample):
    pair = np.hstack([sample, sample])
    with pytest.raises(fs.FaceProblem) as exc:
        fs.analyse(pair, engine)
    assert exc.value.reason == "multiple_faces"


def test_heavy_blur_rejected(engine, sample):
    with pytest.raises(fs.FaceProblem) as exc:
        fs.analyse(cv2.GaussianBlur(sample, (21, 21), 0), engine)
    assert exc.value.reason in {"too_blurry", "no_face"}  # very blurry faces may not even be found


def test_very_dark_photo_rejected(engine, sample):
    with pytest.raises(fs.FaceProblem) as exc:
        fs.analyse(cv2.convertScaleAbs(sample, alpha=0.15, beta=0), engine)
    assert exc.value.reason in {"too_dark", "no_face"}


def test_face_far_away_is_too_small(engine, sample):
    small = cv2.resize(sample, (128, 128), interpolation=cv2.INTER_AREA)
    canvas = np.full((960, 1280, 3), 120, dtype=np.uint8)
    canvas[400:528, 576:704] = small
    with pytest.raises(fs.FaceProblem) as exc:
        fs.analyse(canvas, engine)
    assert exc.value.reason in {"face_too_small", "no_face"}


# ---- liveness with the real detector (Module 4, liveness) ----------------------

def _tilt_flat_picture(image, degrees):
    """Simulate a printed photo or phone screen turned about its vertical axis:
    one side gets narrower and shorter (perspective), like holding it at an angle."""
    import math

    height, width = image.shape[:2]
    shrink = math.sin(math.radians(abs(degrees))) * 0.35
    squeeze = width * (1 - math.cos(math.radians(abs(degrees)))) / 2
    near_side = [[squeeze, 0], [squeeze, height]]
    far_side = [[width - squeeze, height * shrink / 2], [width - squeeze, height * (1 - shrink / 2)]]
    if degrees < 0:
        near_side, far_side = [[squeeze, height * shrink / 2], [squeeze, height * (1 - shrink / 2)]], [[width - squeeze, 0], [width - squeeze, height]]
    src = np.float32([[0, 0], [0, height], [width, 0], [width, height]])
    dst = np.float32([near_side[0], near_side[1], far_side[0], far_side[1]])
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(image, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE)


def test_looking_straight_measures_near_zero(engine, sample):
    from app.services.face_engine import nose_offset

    offset = nose_offset(engine.detect(sample)[0])
    print(f"\nstraight sample: nose offset {offset:+.3f} (straight limit 0.15)")
    assert abs(offset) < 0.15


@pytest.mark.parametrize("degrees", [-40, -25, 25, 40])
def test_tilted_flat_photo_never_looks_like_a_head_turn(engine, sample, degrees):
    from app.services.face_engine import nose_offset

    straight = nose_offset(engine.detect(sample)[0])
    tilted = _tilt_flat_picture(sample, degrees)
    faces = [d for d in engine.detect(tilted) if d.score >= 0.85]
    if not faces:
        pytest.skip("detector did not find the face in this strongly tilted picture (also a pass for liveness)")
    moved = nose_offset(faces[0]) - straight
    print(f"\nflat photo tilted {degrees:+d} deg: moved {moved:+.3f} from straight (a turn needs 0.12)")
    assert abs(moved) < 0.12


def test_photo_attack_fails_full_liveness_check(engine, sample):
    from app.services import face_service

    student = make_user(role=Role.STUDENT, must_change_password=False)
    captures = [encode(sample), encode(cv2.convertScaleAbs(sample, alpha=1.0, beta=15)), encode(_shift(sample, 8, -6))]
    face_service.enrol(student, student, captures, engine)

    straight = [encode(sample), encode(cv2.convertScaleAbs(sample, alpha=0.95, beta=0))]
    left = [encode(_tilt_flat_picture(sample, d)) for d in (20, 30, 35, 40)]
    right = [encode(_tilt_flat_picture(sample, -d)) for d in (20, 30, 35, 40)]
    result = face_service.check_liveness(student, [straight, left, right], "LR", engine)
    print(f"\nphoto attack: identity matched={result.identity_matched} live={result.live} ({result.summary})")
    assert result.identity_matched is True   # it IS the right face...
    assert result.live is False              # ...but a flat picture cannot turn
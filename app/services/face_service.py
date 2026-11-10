"""Face registration and face checks.

Pipeline for every photo:
  1. decode safely (size limits, EXIF rotation, scaled down to at most 1280 px)
  2. find faces; there must be exactly one clear face (small faces far behind,
     like classmates in the background, are ignored)
  3. quality: big enough, fully in the picture, sharp, not too dark or bright
  4. turn the face into 128 numbers (an "embedding")

Registration takes several photos, checks they are all the same person, and
stores their average. Verification compares one new photo with that average.
Photos themselves are never stored.

Thresholds come from app config (FACE_*), so they can be tuned without code changes.
"""
import base64
import binascii
import hashlib
import io
from dataclasses import dataclass

import cv2
import numpy as np
from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

from ..extensions import db
from ..models import AccountEventType, FaceEmbedding, User, record_event
from .common import valid_id
from .errors import Conflict, NotFound, PermissionDenied, ServiceError, ValidationFailed
from .face_engine import EMBEDDING_DIM, MODEL_NAME, OpenCVFaceEngine

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
ALLOWED_DATA_URL_TYPES = {"image/jpeg", "image/png", "image/webp"}
WORKING_MAX_SIDE = 1280     # photos are scaled down to this before analysis (speed)
QUALITY_FACE_SIZE = 112     # face is resized to this before measuring sharpness/brightness
BACKGROUND_FACE_RATIO = 0.25  # other faces smaller than 25% of the main face's area are ignored


# ---- errors ----------------------------------------------------------------

# Plain-language guidance a student can act on.
PROBLEM_MESSAGES = {
    "unreadable_image": "The photo could not be read. Try again.",
    "image_too_large": "The photo is too large. Try again.",
    "image_too_small": "The photo is too small. Try again.",
    "no_face": "No face found. Look straight at the camera in good light.",
    "multiple_faces": "More than one face is in the picture. Make sure only you are in the frame.",
    "face_too_small": "Your face is too small. Move closer to the camera.",
    "face_cut_off": "Your whole face must be inside the picture. Move back a little and centre your face.",
    "too_blurry": "The photo is blurry. Hold the phone still and try again.",
    "too_dark": "The photo is too dark. Move to a brighter place or face the light.",
    "too_bright": "The photo is too bright. Move away from direct light.",
}


class FaceProblem(ValidationFailed):
    """A photo that cannot be used. `reason` is a stable code for the browser."""

    code = "face_problem"

    def __init__(self, reason, *, field="image"):
        self.reason = reason
        super().__init__(PROBLEM_MESSAGES[reason], details={field: [PROBLEM_MESSAGES[reason]]})

    def to_dict(self):
        body = super().to_dict()
        body["reason"] = self.reason
        return body


class FaceServiceUnavailable(ServiceError):
    status_code = 503
    code = "face_service_unavailable"


# ---- engine ----------------------------------------------------------------

def get_engine():
    """The shared face engine, created on first use (models load once per process)."""
    engine = current_app.extensions.get("face_engine")
    if engine is None:
        try:
            engine = OpenCVFaceEngine(current_app.config["FACE_MODEL_DIR"], current_app.config["FACE_MIN_DETECTION_SCORE"])
        except Exception as exc:  # missing files, corrupt model, OpenCV error
            current_app.logger.error("Face engine failed to start: %s", exc)
            raise FaceServiceUnavailable("Face checks are not available right now. Tell your faculty.") from exc
        current_app.extensions["face_engine"] = engine
    return engine


# ---- decoding --------------------------------------------------------------

def decode_data_url(data_url):
    """'data:image/jpeg;base64,....' (what a browser canvas produces) -> raw bytes."""
    if not isinstance(data_url, str) or not data_url.startswith("data:") or "," not in data_url:
        raise FaceProblem("unreadable_image")
    header, _, payload = data_url.partition(",")
    media_type = header[5:].split(";")[0].strip().lower()
    if media_type not in ALLOWED_DATA_URL_TYPES or ";base64" not in header:
        raise FaceProblem("unreadable_image")
    if len(payload) > current_app.config["FACE_MAX_IMAGE_BYTES"] * 4 // 3 + 4:
        raise FaceProblem("image_too_large")
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FaceProblem("unreadable_image") from exc


def decode_image(data):
    """Raw image bytes -> OpenCV BGR array, safely.

    Pillow reads the header first, so a huge or malformed file is rejected
    before any big memory allocation. EXIF rotation is applied, because phones
    often store photos sideways with a "rotate me" note.
    """
    config = current_app.config
    if not isinstance(data, (bytes, bytearray)) or len(data) == 0:
        raise FaceProblem("unreadable_image")
    if len(data) > config["FACE_MAX_IMAGE_BYTES"]:
        raise FaceProblem("image_too_large")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise FaceProblem("unreadable_image")
            width, height = probe.size
            if max(width, height) > config["FACE_MAX_IMAGE_SIDE"]:
                raise FaceProblem("image_too_large")
            if min(width, height) < config["FACE_MIN_IMAGE_SIDE"]:
                raise FaceProblem("image_too_small")
            image = ImageOps.exif_transpose(probe).convert("RGB")
    except FaceProblem:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise FaceProblem("unreadable_image") from exc

    scale = WORKING_MAX_SIDE / max(image.size)
    if scale < 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


# ---- analysis --------------------------------------------------------------

@dataclass
class FaceSample:
    embedding: np.ndarray
    detection_score: float
    sharpness: float
    brightness: float


def _main_face(detections, min_score):
    faces = [d for d in detections if d.score >= min_score]
    if not faces:
        raise FaceProblem("no_face")
    faces.sort(key=lambda d: d.area, reverse=True)
    main = faces[0]
    # A second face of similar size means two people in frame. Much smaller faces
    # (classmates sitting behind) are ignored.
    if any(other.area >= BACKGROUND_FACE_RATIO * main.area for other in faces[1:]):
        raise FaceProblem("multiple_faces")
    return main


def _face_crop(image, face):
    height, width = image.shape[:2]
    x0, y0 = max(int(face.x), 0), max(int(face.y), 0)
    x1, y1 = min(int(face.x + face.width), width), min(int(face.y + face.height), height)
    crop = image[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (QUALITY_FACE_SIZE, QUALITY_FACE_SIZE), interpolation=cv2.INTER_AREA)


def analyse(image, engine=None):
    """One decoded photo -> FaceSample, or FaceProblem explaining what to fix."""
    config = current_app.config
    engine = engine or get_engine()
    face = _main_face(engine.detect(image), config["FACE_MIN_DETECTION_SCORE"])

    height, width = image.shape[:2]
    if min(face.width, face.height) < config["FACE_MIN_SIZE_PX"]:
        raise FaceProblem("face_too_small")
    margin = config["FACE_EDGE_TOLERANCE_PX"]
    if face.x < -margin or face.y < -margin or face.x + face.width > width + margin or face.y + face.height > height + margin:
        raise FaceProblem("face_cut_off")

    gray = _face_crop(image, face)
    brightness = float(gray.mean())
    low, high = config["FACE_BRIGHTNESS_RANGE"]
    if brightness < low:
        raise FaceProblem("too_dark")
    if brightness > high:
        raise FaceProblem("too_bright")
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if sharpness < config["FACE_MIN_SHARPNESS"]:
        raise FaceProblem("too_blurry")

    embedding = engine.embed(image, face)
    return FaceSample(embedding=embedding, detection_score=face.score, sharpness=sharpness, brightness=brightness)


def similarity(a, b):
    """Cosine similarity of two unit-length embeddings: 1 = identical, ~0 = unrelated."""
    return float(np.clip(np.dot(a, b), -1.0, 1.0))


def to_bytes(vector):
    return np.asarray(vector, dtype="<f4").tobytes()


def from_bytes(data):
    return np.frombuffer(data, dtype="<f4")


# ---- registration ----------------------------------------------------------

def enrol(actor, student, images, engine=None):
    """Register the student's face from several photos taken one after another.

    images: list of raw image bytes. Students register their own face; no approval
    is needed. A face that is already registered must be reset by faculty or the
    admin first (so nobody can quietly replace someone's face).
    """
    config = current_app.config
    if actor is None or not actor.is_student or actor.id != student.id or not actor.is_active:
        raise PermissionDenied("Students register their own face only.")
    if student.face is not None:
        raise Conflict(
            "Your face is already registered.",
            details={"_body": ["Your face is already registered. If it needs redoing, ask your faculty to reset it."]},
        )

    needed = config["FACE_ENROL_CAPTURES"]
    if not isinstance(images, list) or len(images) != needed:
        raise ValidationFailed("Wrong number of photos.", details={"images": [f"Send exactly {needed} photos."]})
    if not all(isinstance(i, (bytes, bytearray)) and len(i) > 0 for i in images):
        raise ValidationFailed("Unreadable photos.", details={"images": [PROBLEM_MESSAGES["unreadable_image"]]})
    if len({hashlib.sha256(bytes(i)).digest() for i in images}) != needed:
        # The exact same file sent twice is not a new capture (e.g. one photo uploaded three times).
        raise ValidationFailed(
            "Photos are identical.", details={"images": ["Each photo must be a new capture. Take the photos again."]}
        )

    engine = engine or get_engine()
    samples, errors = [], {}
    for index, data in enumerate(images):
        try:
            samples.append(analyse(decode_image(data), engine))
        except FaceProblem as problem:
            errors[str(index)] = [problem.message]
    if errors:
        # Every unusable photo is reported by its position, with what to fix.
        raise ValidationFailed("Some photos could not be used.", details={"images": errors})

    threshold = config["FACE_MATCH_THRESHOLD"]
    for i in range(len(samples)):
        for j in range(i + 1, len(samples)):
            if similarity(samples[i].embedding, samples[j].embedding) < threshold:
                raise ValidationFailed(
                    "Photos do not match each other.",
                    details={"images": ["The photos do not look like the same person. Take them again, alone in the frame."]},
                )

    average = np.mean([s.embedding for s in samples], axis=0)
    average = average / np.linalg.norm(average)
    quality = float(np.clip(np.mean([s.detection_score for s in samples]), 0.0, 1.0))

    record = FaceEmbedding(student=student, model_name=MODEL_NAME, dim=EMBEDDING_DIM, embedding=to_bytes(average), quality=quality)
    db.session.add(record)
    db.session.commit()
    current_app.logger.info("face registered for user %s (quality %.2f)", student.id, quality)
    return record


# ---- verification ----------------------------------------------------------

@dataclass
class VerifyResult:
    matched: bool
    similarity: float
    distance: float  # 1 - similarity; stored on attendance records as evidence


class FaceNotRegistered(ValidationFailed):
    code = "face_not_registered"


def verify(student, image_data, engine=None):
    """Compare one new photo with the student's registered face."""
    record = student.face
    if record is None:
        raise FaceNotRegistered("Register your face first.", details={"_body": ["Your face is not registered yet."]})
    if record.model_name != MODEL_NAME or record.dim != EMBEDDING_DIM:
        # Registered with a different face model: the numbers are not comparable.
        raise FaceNotRegistered(
            "Your face needs to be registered again.",
            details={"_body": ["The face system was updated. Ask your faculty to reset your face registration."]},
        )
    sample = analyse(decode_image(image_data), engine)
    score = similarity(sample.embedding, from_bytes(record.embedding))
    matched = score >= current_app.config["FACE_MATCH_THRESHOLD"]
    return VerifyResult(matched=matched, similarity=score, distance=max(0.0, 1.0 - score))


# ---- reset -----------------------------------------------------------------

RESET_REASON_MIN = 3
RESET_REASON_MAX = 200


def can_reset_face(actor, student):
    """Admin: any student. Faculty: students actively enrolled in an active course they teach."""
    from .user_service import faculty_teaches_student

    if actor is None or not actor.is_active or student is None or not student.is_student:
        return False
    if actor.is_admin:
        return True
    return actor.is_faculty and faculty_teaches_student(actor, student)


def reset_face(actor, student_id, reason):
    """Delete a student's face registration so they register again at next login.

    The reason is required and recorded with who reset it and when
    (account_events). The student is not logged out: on their next page they
    are simply sent to the registration page.
    """
    student = db.session.get(User, student_id) if valid_id(student_id) else None
    if student is None or not student.is_student:
        raise NotFound("Student not found.")
    if not can_reset_face(actor, student):
        raise PermissionDenied("You can only reset the face of students in courses you teach.")

    reason = reason.strip() if isinstance(reason, str) else ""
    if not RESET_REASON_MIN <= len(reason) <= RESET_REASON_MAX:
        raise ValidationFailed(
            "Give a reason.",
            details={"reason": [f"Say why the face is being reset ({RESET_REASON_MIN}-{RESET_REASON_MAX} characters)."]},
        )
    if student.face is None:
        raise NotFound(f"{student.username} has no face registered.")

    db.session.delete(student.face)
    record_event(student, actor, AccountEventType.FACE_RESET, reason)
    db.session.commit()
    db.session.expire(student, ["face"])
    current_app.logger.info("face reset for user %s by %s", student.id, actor.username)
    return student
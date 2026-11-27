"""Helpers for face tests: synthetic photos and a stand-in face engine."""
import io

import cv2
import numpy as np
from PIL import Image

from app.services.face_engine import EMBEDDING_DIM, Detection


def photo(width=640, height=480, brightness=128, seed=0, blur=0):
    """A synthetic 'photo' with fine texture (sharp) at the given brightness."""
    rng = np.random.default_rng(seed)
    noise = rng.integers(-60, 60, size=(height, width), dtype=np.int16)
    gray = np.clip(brightness + noise, 0, 255).astype(np.uint8)
    image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if blur:
        image = cv2.GaussianBlur(image, (blur, blur), 0)
    return image


def encode(image, fmt="JPEG", **save_kwargs):
    rgb = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    buffer = io.BytesIO()
    rgb.save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue()


def face(x=200, y=120, size=200, score=0.95, offset=0.0):
    """A detected face with YuNet-style landmarks. `offset` = nose position between the
    eyes in eye-widths: 0 looking straight, about +/-0.12 or more for a real head turn."""
    eye_left, eye_right = x + 0.3 * size, x + 0.7 * size
    eye_y, eye_width = y + 0.4 * size, 0.4 * size
    nose_x = (eye_left + eye_right) / 2 + offset * eye_width
    raw = np.array(
        [x, y, size, size, eye_left, eye_y, eye_right, eye_y, nose_x, y + 0.6 * size,
         x + 0.35 * size, y + 0.8 * size, x + 0.65 * size, y + 0.8 * size, score],
        dtype=np.float32,
    )
    return Detection(x=x, y=y, width=size, height=size, score=score, raw=raw)


def unit(seed):
    vector = np.random.default_rng(seed).normal(size=EMBEDDING_DIM).astype(np.float32)
    return vector / np.linalg.norm(vector)


def near(vector, noise=0.05, seed=1):
    """A vector close to `vector` (same person, slightly different photo)."""
    jitter = np.random.default_rng(seed).normal(scale=noise, size=vector.shape).astype(np.float32)
    out = vector + jitter
    return out / np.linalg.norm(out)


class FakeEngine:
    """Stands in for OpenCVFaceEngine. Returns the faces and embeddings it is given.

    offsets: optional list of nose offsets, one per detect() call in order, to
    simulate a head turning (or not) across the frames of a liveness check.
    """

    def __init__(self, detections=None, embeddings=None, offsets=None):
        self.detections = [face()] if detections is None else detections
        self.embeddings = list(embeddings) if embeddings else []
        self.offsets = list(offsets) if offsets else []
        self.default = unit(42)

    def detect(self, image):
        if self.offsets and self.detections:
            main = self.detections[0]
            turned = face(x=main.x, y=main.y, size=main.width, score=main.score, offset=self.offsets.pop(0))
            return [turned] + list(self.detections[1:])
        return list(self.detections)

    def embed(self, image, detection):
        return self.embeddings.pop(0) if self.embeddings else self.default
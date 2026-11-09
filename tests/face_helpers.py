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


def face(x=200, y=120, size=200, score=0.95):
    return Detection(x=x, y=y, width=size, height=size, score=score, raw=np.zeros(15, dtype=np.float32))


def unit(seed):
    vector = np.random.default_rng(seed).normal(size=EMBEDDING_DIM).astype(np.float32)
    return vector / np.linalg.norm(vector)


def near(vector, noise=0.05, seed=1):
    """A vector close to `vector` (same person, slightly different photo)."""
    jitter = np.random.default_rng(seed).normal(scale=noise, size=vector.shape).astype(np.float32)
    out = vector + jitter
    return out / np.linalg.norm(out)


class FakeEngine:
    """Stands in for OpenCVFaceEngine. Returns the faces and embeddings it is given."""

    def __init__(self, detections=None, embeddings=None):
        self.detections = [face()] if detections is None else detections
        self.embeddings = list(embeddings) if embeddings else []
        self.default = unit(42)

    def detect(self, image):
        return list(self.detections)

    def embed(self, image, detection):
        return self.embeddings.pop(0) if self.embeddings else self.default
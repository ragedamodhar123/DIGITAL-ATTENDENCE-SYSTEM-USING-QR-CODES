"""The face models: OpenCV YuNet (finds faces) and SFace (describes a face as 128 numbers).

This file only talks to the models. The rules (quality checks, enrol, verify)
live in face_service.py, which takes an engine object. Tests pass in a stand-in
engine, so the rules are tested without the 40 MB model files.

Thread safety: OpenCV's DNN networks are not safe to call from several threads
at once, and waitress serves requests on several threads. One lock around each
model call keeps them safe. Each call takes ~10-40 ms on a laptop CPU, so a
class of 60 students queueing briefly is fine.
"""
import hashlib
import os
import threading
from dataclasses import dataclass

import cv2
import numpy as np

MODEL_NAME = "opencv-sface-2021dec"  # stored with every embedding; see FaceEmbedding.model_name
EMBEDDING_DIM = 128

YUNET_FILE = "face_detection_yunet_2023mar.onnx"
SFACE_FILE = "face_recognition_sface_2021dec.onnx"

# Official files from github.com/opencv/opencv_zoo, with the checksums published there.
MODEL_FILES = {
    YUNET_FILE: {
        "url": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        "size": 232589,
    },
    SFACE_FILE: {
        "url": "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "sha256": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        "size": 38696353,
    },
}


class FaceModelsMissing(RuntimeError):
    """The model files are not downloaded (or are damaged)."""


@dataclass
class Detection:
    """One face found in an image."""

    x: float
    y: float
    width: float
    height: float
    score: float           # detector confidence, 0..1
    raw: np.ndarray        # the detector's full row (box + 5 landmarks + score), needed for alignment

    @property
    def area(self):
        return self.width * self.height


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def missing_or_damaged_models(model_dir):
    """Return the list of model files that are absent or do not match the official checksum."""
    problems = []
    for name, info in MODEL_FILES.items():
        path = os.path.join(model_dir, name)
        if not os.path.isfile(path) or os.path.getsize(path) != info["size"] or file_sha256(path) != info["sha256"]:
            problems.append(name)
    return problems


class OpenCVFaceEngine:
    def __init__(self, model_dir, min_score=0.85):
        problems = missing_or_damaged_models(model_dir)
        if problems:
            raise FaceModelsMissing(
                f"Face model files missing or damaged in {model_dir}: {', '.join(problems)}. "
                "Run: flask --app wsgi download-face-models"
            )
        self._lock = threading.Lock()
        self._detector = cv2.FaceDetectorYN.create(
            os.path.join(model_dir, YUNET_FILE), "", (320, 320), min_score, 0.3, 5000
        )
        self._recognizer = cv2.FaceRecognizerSF.create(os.path.join(model_dir, SFACE_FILE), "")

    def detect(self, image_bgr):
        """All faces in the image, as Detection objects (possibly empty)."""
        height, width = image_bgr.shape[:2]
        with self._lock:
            self._detector.setInputSize((width, height))
            _, faces = self._detector.detect(image_bgr)
        if faces is None:
            return []
        return [
            Detection(x=float(f[0]), y=float(f[1]), width=float(f[2]), height=float(f[3]), score=float(f[14]), raw=f)
            for f in faces
        ]

    def embed(self, image_bgr, detection):
        """128 numbers describing the face, scaled to length 1 (so cosine = dot product)."""
        with self._lock:
            aligned = self._recognizer.alignCrop(image_bgr, detection.raw)
            feature = self._recognizer.feature(aligned)
        vector = np.asarray(feature, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if norm == 0.0 or not np.isfinite(norm):
            raise ValueError("Face model returned an empty description.")
        return vector / norm
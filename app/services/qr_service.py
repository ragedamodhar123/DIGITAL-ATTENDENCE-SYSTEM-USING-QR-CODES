"""Rotating QR tokens for attendance sessions.

A token looks like  "<session id>.<window>.<signature>", e.g. "42.176309210.Xy3...".

- window = current time divided into QR_ROTATION_SECONDS slots (10 s), so the
  code changes every 10 seconds.
- signature = HMAC-SHA256 over "<session id>.<window>", keyed by the session's
  own random secret mixed with the app's SECRET_KEY. Nobody can make a valid
  code without both, and a code from one session never works for another.
- A code is accepted during its own window and QR_GRACE_WINDOWS windows after
  (so a scan at 9.9 s still counts): 10 to 20 seconds of life in total.
- Nothing is stored per code: the server recomputes the signature to check it.

The QR itself contains a full link (https://<site>/a/<token>), so it also works
with the phone's normal camera app, not only the in-app scanner (Module 4, part 3).
"""
import base64
import hashlib
import hmac
import io
import math
import re

import qrcode
import qrcode.image.svg
from flask import current_app

from ..extensions import db
from ..models import AttendanceSession, SessionStatus
from ..utils.timeutils import utcnow
from .errors import ValidationFailed

SCAN_PATH = "/a/"            # the student check-in page (Module 4, part 3)
SIGNATURE_BYTES = 16         # 128 bits: unguessable, keeps the QR small
MAX_TOKEN_LENGTH = 80
TOKEN_PATTERN = re.compile(r"^(\d{1,10})\.(\d{1,12})\.([A-Za-z0-9_-]{22})$")

TOKEN_MESSAGES = {
    "invalid_code": "This is not a valid attendance code. Scan the code on your faculty's screen.",
    "expired_code": "This code has expired. Scan the code currently on the screen.",
    "session_closed": "Attendance for this class has ended.",
}


class TokenProblem(ValidationFailed):
    """A code that cannot be used. `reason` is a stable code for the browser."""

    code = "attendance_code_problem"

    def __init__(self, reason):
        self.reason = reason
        super().__init__(TOKEN_MESSAGES[reason], details={"code": [TOKEN_MESSAGES[reason]]})

    def to_dict(self):
        body = super().to_dict()
        body["reason"] = self.reason
        return body


# ---- time windows ----------------------------------------------------------

def _rotation():
    return current_app.config["QR_ROTATION_SECONDS"]


def window_at(now):
    return int(now.timestamp() // _rotation())


def ms_until_next_window(now):
    rotation = _rotation()
    elapsed = now.timestamp() % rotation
    return max(1, math.ceil((rotation - elapsed) * 1000))


# ---- signing ---------------------------------------------------------------

def _key(session):
    # Session secret mixed with the app SECRET_KEY: a copy of the database alone
    # is not enough to forge codes.
    app_key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(app_key, session.qr_secret.encode(), hashlib.sha256).digest()


def _signature(session, window):
    message = f"{session.id}.{window}".encode()
    digest = hmac.new(_key(session), message, hashlib.sha256).digest()[:SIGNATURE_BYTES]
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def make_token(session, now=None):
    """The code for the current window. Returns (token, ms until it changes)."""
    if session.status != SessionStatus.OPEN or session.qr_secret is None:
        raise ValueError("Only open live sessions have QR codes.")
    now = now or utcnow()
    window = window_at(now)
    return f"{session.id}.{window}.{_signature(session, window)}", ms_until_next_window(now)


def verify_token(token, now=None):
    """Return the open session a code belongs to, or raise TokenProblem."""
    now = now or utcnow()
    if not isinstance(token, str) or len(token) > MAX_TOKEN_LENGTH:
        raise TokenProblem("invalid_code")
    match = TOKEN_PATTERN.match(token.strip())
    if not match:
        raise TokenProblem("invalid_code")
    session_id, window, signature = int(match.group(1)), int(match.group(2)), match.group(3)

    session = db.session.get(AttendanceSession, session_id) if session_id > 0 else None
    if session is None or session.qr_secret is None:
        raise TokenProblem("invalid_code")
    # Constant-time comparison: timing does not reveal how much of a forged code was right.
    if not hmac.compare_digest(signature, _signature(session, window)):
        raise TokenProblem("invalid_code")

    if session.status != SessionStatus.OPEN or session.expires_at <= now:
        raise TokenProblem("session_closed")
    current = window_at(now)
    if window > current or window < current - current_app.config["QR_GRACE_WINDOWS"]:
        raise TokenProblem("expired_code")
    return session


# ---- QR image --------------------------------------------------------------

def scan_url(host_url, token):
    """Full link inside the QR. host_url comes from the request (e.g. the tunnel address)."""
    return host_url.rstrip("/") + SCAN_PATH + token


def qr_svg(text):
    """An SVG QR code that scales cleanly to any size (projector-friendly)."""
    image = qrcode.make(
        text,
        image_factory=qrcode.image.svg.SvgPathImage,
        error_correction=qrcode.constants.ERROR_CORRECT_M,  # still scans if partly glared/obscured
        border=2,
    )
    buffer = io.BytesIO()
    image.save(buffer)
    svg = buffer.getvalue().decode()
    svg = svg[svg.index("<svg"):]  # drop the XML header so it can sit inside HTML
    # Let CSS decide the size (the library writes fixed millimetre sizes).
    svg = re.sub(r'\s(width|height)="[^"]*"', "", svg, count=2)
    return svg.replace("<svg", '<svg role="img" aria-label="Attendance QR code"', 1)
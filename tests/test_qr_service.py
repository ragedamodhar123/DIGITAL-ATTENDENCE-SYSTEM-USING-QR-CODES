"""Module 4, part 2: rotating QR tokens."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Role
from app.services import qr_service as qr
from app.services import session_service
from tests.factories import assign_faculty, enroll, make_course, make_user

T0 = datetime(2026, 11, 10, 4, 0, 0, tzinfo=timezone.utc)  # exactly on a 10 s boundary


@pytest.fixture
def live():
    faculty = make_user(role=Role.FACULTY)
    course = make_course()
    assign_faculty(course, faculty)
    enroll(course, make_user(role=Role.STUDENT))
    session, _ = session_service.start_session(faculty, course.id, now=T0)
    return faculty, session


def _reason(call):
    with pytest.raises(qr.TokenProblem) as exc:
        call()
    return exc.value.reason


def test_token_round_trip(live):
    _, session = live
    token, _ = qr.make_token(session, T0)
    assert qr.verify_token(token, T0) == session
    assert len(token) < 50  # short link -> small, easy-to-scan QR


def test_code_changes_every_window_and_stays_same_within_one(live):
    _, session = live
    a, _ = qr.make_token(session, T0)
    b, _ = qr.make_token(session, T0 + timedelta(seconds=9.9))
    c, _ = qr.make_token(session, T0 + timedelta(seconds=10))
    assert a == b and b != c


def test_time_until_next_code(live):
    _, session = live
    assert qr.make_token(session, T0 + timedelta(seconds=3))[1] == 7000
    assert qr.make_token(session, T0 + timedelta(seconds=9.95))[1] == 50


def test_scan_just_after_change_still_counts(live):
    # Code shown at 9.9 s, scanned at 10.5 s: one window late, inside the grace period.
    _, session = live
    token, _ = qr.make_token(session, T0 + timedelta(seconds=9.9))
    assert qr.verify_token(token, T0 + timedelta(seconds=10.5)) == session
    assert qr.verify_token(token, T0 + timedelta(seconds=19.9)) == session


def test_old_code_expires_after_grace(live):
    _, session = live
    token, _ = qr.make_token(session, T0)
    assert _reason(lambda: qr.verify_token(token, T0 + timedelta(seconds=20))) == "expired_code"
    assert _reason(lambda: qr.verify_token(token, T0 + timedelta(minutes=5))) == "expired_code"


def test_code_from_the_future_rejected(live):
    _, session = live
    token, _ = qr.make_token(session, T0 + timedelta(seconds=30))
    assert _reason(lambda: qr.verify_token(token, T0)) == "expired_code"


def test_forged_or_tampered_codes(live):
    _, session = live
    token, _ = qr.make_token(session, T0)
    sid, window, sig = token.split(".")
    flipped = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    bad = [
        f"{sid}.{window}.{flipped}",                       # signature changed
        f"{sid}.{int(window) + 1}.{sig}",                  # window changed
        f"{int(sid) + 1}.{window}.{sig}",                  # other session
        "", "garbage", "1.2.3", f"{sid}.{window}", "x" * 200, None, 42,
        f"-{sid}.{window}.{sig}", f"{sid}.{window}.{sig}extra",
    ]
    for value in bad:
        assert _reason(lambda: qr.verify_token(value, T0)) == "invalid_code", value


def test_code_from_another_session_cannot_be_reused(live):
    # Two classes running at once: a code for DS301 relabelled as DB302's session fails.
    _, session_a = live
    other_faculty = make_user(role=Role.FACULTY)
    other_course = make_course()
    assign_faculty(other_course, other_faculty)
    enroll(other_course, make_user(role=Role.STUDENT))
    session_b, _ = session_service.start_session(other_faculty, other_course.id, now=T0)

    token_a, _ = qr.make_token(session_a, T0)
    _, window, signature = token_a.split(".")
    relabelled = f"{session_b.id}.{window}.{signature}"
    assert _reason(lambda: qr.verify_token(relabelled, T0)) == "invalid_code"
    assert qr.verify_token(qr.make_token(session_b, T0)[0], T0) == session_b


def test_closed_or_expired_session(live):
    faculty, session = live
    token, _ = qr.make_token(session, T0)
    session_service.stop_session(faculty, session.id, now=T0 + timedelta(seconds=2))
    assert _reason(lambda: qr.verify_token(token, T0 + timedelta(seconds=3))) == "session_closed"


def test_code_past_session_expiry_rejected(live):
    _, session = live
    late = session.expires_at + timedelta(seconds=1)
    token, _ = qr.make_token(session, late)
    assert _reason(lambda: qr.verify_token(token, late)) == "session_closed"


def test_codes_depend_on_app_secret_key(live, app, monkeypatch):
    # A copy of the database (which holds the session secret) is not enough to forge codes.
    _, session = live
    token, _ = qr.make_token(session, T0)
    monkeypatch.setitem(app.config, "SECRET_KEY", "a-completely-different-secret-key")
    assert _reason(lambda: qr.verify_token(token, T0)) == "invalid_code"


def test_messages_and_reason_in_json(live):
    problem = qr.TokenProblem("expired_code")
    assert "Scan the code currently on the screen" in problem.message
    assert problem.to_dict()["reason"] == "expired_code" and problem.status_code == 422


def test_qr_svg_and_link(live):
    _, session = live
    token, _ = qr.make_token(session, T0)
    url = qr.scan_url("https://abc.trycloudflare.com/", token)
    assert url == f"https://abc.trycloudflare.com/a/{token}"
    svg = qr.qr_svg(url)
    assert svg.startswith("<svg") and "viewBox" in svg
    assert 'width="' not in svg.split(">", 1)[0]  # sized by CSS, not fixed millimetres
    assert "<?xml" not in svg


def test_qr_content_decodes_back_to_the_link(live):
    # Build the same QR (same settings as qr_svg) as pixels and read it with OpenCV's
    # QR detector: the link survives encoding with the error-correction level we use.
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    _, session = live
    token, _ = qr.make_token(session, T0)
    url = qr.scan_url("https://abc.trycloudflare.com", token)
    import qrcode

    matrix = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2)
    matrix.add_data(url)
    matrix.make()
    cells = np.array(matrix.get_matrix(), dtype=np.uint8)
    image = np.kron(1 - cells, np.ones((10, 10), dtype=np.uint8)) * 255
    decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
    assert decoded == url
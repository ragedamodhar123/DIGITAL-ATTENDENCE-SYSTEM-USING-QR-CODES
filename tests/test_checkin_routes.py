"""Module 4, part 3: the /a/<code> page, the scanner page and the student home."""
import struct

import pytest

from app.extensions import db
from app.models import AttendanceSession, FaceEmbedding, PendingCheckin, Role, ScanLog
from app.services import qr_service, session_service
from tests.factories import TEST_PASSWORD, assign_faculty, enroll, make_course, make_record, make_user


def _face(student):
    db.session.add(FaceEmbedding(student=student, model_name="t", dim=128, embedding=struct.pack("<128f", *[0.0] * 128)))


@pytest.fixture
def live():
    faculty = make_user(role=Role.FACULTY, username="fac001", must_change_password=False)
    course = make_course(code="DS301", name="Data Structures")
    assign_faculty(course, faculty)
    student = make_user(role=Role.STUDENT, username="stu001", must_change_password=False)
    enroll(course, student)
    _face(student)
    db.session.commit()
    session, _ = session_service.start_session(faculty, course.id)
    return faculty, course, student, session


def _login(client, username="stu001"):
    client.post("/login", data={"username": username, "password": TEST_PASSWORD})


def _code(session):
    return qr_service.make_token(session)[0]


# ---- /a/<code> ---------------------------------------------------------------

def test_scan_link_accepts_code_and_starts_check_in(client, live):
    _, _, student, session = live
    _login(client)
    resp = client.get(f"/a/{_code(session)}")
    page = resp.data.decode()
    assert resp.status_code == 200
    assert "Code accepted" in page and "DS301 Data Structures" in page
    assert f'data-session-id="{session.id}"' in page
    assert PendingCheckin.query.filter_by(student_id=student.id).count() == 1


def test_logged_out_student_is_sent_to_login_and_back(client, live):
    _, _, _, session = live
    code = _code(session)
    resp = client.get(f"/a/{code}")
    assert resp.status_code == 302 and resp.headers["Location"].startswith("/login?next=")
    assert code in resp.headers["Location"]
    back = client.post("/login", data={"username": "stu001", "password": TEST_PASSWORD, "next": f"/a/{code}"})
    assert back.headers["Location"] == f"/a/{code}"


@pytest.mark.parametrize(
    "code,reason_text,status",
    [
        ("garbage", b"Not an attendance code", 422),
        ("1.2.AAAAAAAAAAAAAAAAAAAAAA", b"Not an attendance code", 422),
    ],
)
def test_bad_codes_show_a_clear_page(client, live, code, reason_text, status):
    _login(client)
    resp = client.get(f"/a/{code}")
    assert resp.status_code == status and reason_text in resp.data and b"Scan again" in resp.data


def test_code_for_ended_attendance(client, live):
    faculty, _, _, session = live
    code = _code(session)
    session_service.stop_session(faculty, session.id)
    _login(client)
    resp = client.get(f"/a/{code}")
    assert resp.status_code == 422 and b"Attendance has ended" in resp.data


def test_student_of_another_course(client, live):
    _, _, _, session = live
    _face(make_user(role=Role.STUDENT, username="stu999", must_change_password=False))
    db.session.commit()
    _login(client, "stu999")
    resp = client.get(f"/a/{_code(session)}")
    assert resp.status_code == 403 and b"Not your course" in resp.data


def test_already_present(client, live):
    _, _, student, session = live
    make_record(session=session, student=student, status="present", method="qr_face")
    db.session.commit()
    _login(client)
    resp = client.get(f"/a/{_code(session)}")
    assert resp.status_code == 200 and b"Already marked present" in resp.data


def test_faculty_cannot_use_the_scan_link(client, live):
    _, _, _, session = live
    _login(client, "fac001")
    assert client.get(f"/a/{_code(session)}").status_code == 403


def test_student_without_face_is_sent_to_register(client, live):
    _, course, _, session = live
    newcomer = make_user(role=Role.STUDENT, username="new001", must_change_password=False)
    enroll(course, newcomer)
    db.session.commit()
    _login(client, "new001")
    assert client.get(f"/a/{_code(session)}").headers["Location"] == "/face/enroll"


def test_scans_are_rate_limited_per_student(client, live):
    _login(client)
    codes = [client.get("/a/garbage").status_code for _ in range(31)]
    assert codes[:30] == [422] * 30 and codes[30] == 429


def test_every_scan_is_logged_with_ip(client, live):
    _, _, _, session = live
    _login(client)
    client.get("/a/garbage", environ_base={"REMOTE_ADDR": "10.1.2.3"})
    client.get(f"/a/{_code(session)}", environ_base={"REMOTE_ADDR": "10.1.2.3"})
    logs = [(l.outcome, l.ip_address) for l in ScanLog.query.order_by(ScanLog.id)]
    assert logs == [("invalid_code", "10.1.2.3"), ("ok", "10.1.2.3")]


def test_check_in_page_is_not_cached(client, live):
    _, _, _, session = live
    _login(client)
    assert "no-store" in client.get(f"/a/{_code(session)}").headers["Cache-Control"]


# ---- scanner page and home ---------------------------------------------------

def test_scanner_page(client, live):
    _login(client)
    page = client.get("/student/scan").data.decode()
    assert 'id="qr-reader"' in page and "html5-qrcode.min.js" in page and "student-scan.js" in page


def test_home_shows_open_attendance_and_scan_button(client, live):
    faculty, _, _, session = live
    _login(client)
    page = client.get("/student/").data.decode()
    assert "Attendance is open" in page and "DS301 Data Structures" in page and "Scan attendance code" in page
    session_service.stop_session(faculty, session.id)
    page = client.get("/student/").data.decode()
    assert "Attendance is open" not in page and "Scan attendance code" in page


def test_scanner_script_is_served(client):
    assert b"checkinPathFrom" in client.get("/static/js/student-scan.js").data
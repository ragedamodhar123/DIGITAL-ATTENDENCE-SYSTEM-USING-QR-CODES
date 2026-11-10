"""Module 3, part 3: resetting face registration, and the account history."""
import struct

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.extensions import db
from app.models import AccountEvent, Enrollment, FaceEmbedding, Role
from app.services import face_service, user_service
from app.services.errors import NotFound, PermissionDenied, ValidationFailed
from app.utils.security import generate_api_token
from tests.factories import TEST_PASSWORD, assign_faculty, enroll, make_course, make_user


def _give_face(student):
    db.session.add(FaceEmbedding(student=student, model_name="t", dim=128, embedding=struct.pack("<128f", *[0.0] * 128)))
    db.session.flush()


@pytest.fixture
def setup():
    """A faculty member teaching one course with one registered student."""
    faculty = make_user(role=Role.FACULTY, username="fac001", must_change_password=False)
    student = make_user(role=Role.STUDENT, username="stu001", must_change_password=False, full_name="Asha Nair")
    course = make_course(code="DS301")
    assign_faculty(course, faculty)
    enroll(course, student)
    _give_face(student)
    return faculty, student, course


# ---- service ---------------------------------------------------------------

def test_faculty_resets_face_of_own_student_and_it_is_recorded(setup):
    faculty, student, _ = setup
    face_service.reset_face(faculty, student.id, "  Face check keeps failing  ")
    assert student.face is None
    event = AccountEvent.query.one()
    assert (event.event, event.reason, event.actor, event.user) == ("face_reset", "Face check keeps failing", faculty, student)
    assert event.created_at is not None


def test_admin_resets_any_student():
    admin = make_user(role=Role.ADMIN)
    student = make_user(role=Role.STUDENT)
    _give_face(student)
    face_service.reset_face(admin, student.id, "New photo needed")
    assert student.face is None


def test_faculty_cannot_reset_other_students(setup):
    faculty, _, _ = setup
    stranger = make_user(role=Role.STUDENT)
    _give_face(stranger)
    with pytest.raises(PermissionDenied):
        face_service.reset_face(faculty, stranger.id, "No reason really")
    assert stranger.face is not None and AccountEvent.query.count() == 0


def test_dropped_enrolment_removes_reset_right(setup):
    faculty, student, _ = setup
    Enrollment.query.filter_by(student_id=student.id).one().is_active = False
    with pytest.raises(PermissionDenied):
        face_service.reset_face(faculty, student.id, "Valid reason")


def test_students_cannot_reset_faces(setup):
    _, student, _ = setup
    with pytest.raises(PermissionDenied):
        face_service.reset_face(student, student.id, "I want a new one")


@pytest.mark.parametrize("reason", ["", "  ", "ab", None, "x" * 201])
def test_reason_is_required(setup, reason):
    faculty, student, _ = setup
    with pytest.raises(ValidationFailed) as exc:
        face_service.reset_face(faculty, student.id, reason)
    assert "reason" in exc.value.details and student.face is not None


def test_reset_without_registered_face_is_404(setup):
    faculty, student, _ = setup
    face_service.reset_face(faculty, student.id, "First reset")
    with pytest.raises(NotFound):
        face_service.reset_face(faculty, student.id, "Second reset")


@pytest.mark.parametrize("target", ["faculty", "missing", "bool"])
def test_reset_only_targets_students(setup, target):
    faculty, _, _ = setup
    admin = make_user(role=Role.ADMIN)
    target_id = {"faculty": faculty.id, "missing": 99999, "bool": True}[target]
    with pytest.raises(NotFound):
        face_service.reset_face(admin, target_id, "Valid reason")


def test_student_can_register_again_after_reset(setup):
    from tests.face_helpers import FakeEngine, encode, near, photo, unit

    faculty, student, _ = setup
    face_service.reset_face(faculty, student.id, "Bad registration")
    images = [encode(photo(seed=i)) for i in range(3)]
    face_service.enrol(student, student, images, FakeEngine(embeddings=[near(unit(3), seed=s) for s in (1, 2, 3)]))
    assert student.face is not None


# ---- account history -------------------------------------------------------

def test_password_issue_and_deactivation_are_recorded(setup):
    faculty, student, _ = setup
    admin = make_user(role=Role.ADMIN, username="admin1")
    user_service.issue_temporary_password(faculty, student.id)
    user_service.set_active(admin, student.id, False)
    user_service.set_active(admin, student.id, False)  # no change: not recorded twice
    user_service.set_active(admin, student.id, True)

    _, events = user_service.account_history(admin, student.id)
    assert [(e.event, e.actor.username) for e in events] == [
        ("activated", "admin1"),
        ("deactivated", "admin1"),
        ("password_issued", "fac001"),
    ]


def test_history_is_admin_only(setup):
    faculty, student, _ = setup
    with pytest.raises(PermissionDenied):
        user_service.account_history(faculty, student.id)


def test_history_rows_cannot_be_changed_or_deleted(setup):
    faculty, student, _ = setup
    face_service.reset_face(faculty, student.id, "Bad registration")
    for statement in ("UPDATE account_events SET reason = 'rewritten'", "DELETE FROM account_events"):
        with pytest.raises(DBAPIError) as exc:
            db.session.execute(text(statement))
        db.session.rollback()
        assert "append-only" in str(exc.value)


def test_face_reset_row_without_reason_rejected_by_database(setup):
    faculty, student, _ = setup
    db.session.add(AccountEvent(user=student, actor=faculty, event="face_reset", reason=None))
    with pytest.raises(IntegrityError) as exc:
        db.session.flush()
    db.session.rollback()
    assert "face_reset_has_reason" in str(exc.value)


# ---- admin API -------------------------------------------------------------

@pytest.fixture
def token():
    token, token_hash = generate_api_token()
    make_user(role=Role.ADMIN, username="admin1", api_token_hash=token_hash)
    db.session.commit()
    return token


def test_admin_api_reset_and_history(client, token, setup):
    _, student, _ = setup
    db.session.commit()
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(f"/api/admin/users/{student.id}/face/reset", json={"reason": "Photo was blurry"}, headers=headers)
    assert resp.status_code == 200 and resp.get_json()["user"]["face_enrolled"] is False

    again = client.post(f"/api/admin/users/{student.id}/face/reset", json={"reason": "Again"}, headers=headers)
    assert again.status_code == 404

    no_reason = client.post(f"/api/admin/users/{student.id}/face/reset", json={}, headers=headers)
    assert no_reason.status_code == 422 and "reason" in no_reason.get_json()["details"]

    history = client.get(f"/api/admin/users/{student.id}/history", headers=headers).get_json()
    assert history["events"][0]["event"] == "face_reset"
    assert history["events"][0]["by"] == "admin1" and history["events"][0]["reason"] == "Photo was blurry"


# ---- faculty page ----------------------------------------------------------

def _faculty_login(client):
    client.post("/login", data={"username": "fac001", "password": TEST_PASSWORD})


def test_faculty_page_shows_reset_only_for_registered_students(client, setup):
    _, _, course = setup
    make_user(role=Role.STUDENT, username="stu002", must_change_password=False)
    enroll(course, make_user(role=Role.STUDENT, username="stu003", must_change_password=False))
    db.session.commit()
    _faculty_login(client)
    page = client.get(f"/faculty/courses/{course.id}").data.decode()
    assert page.count("Reset face</button>") == 1  # only stu001 has a face


def test_faculty_resets_face_from_page_and_student_must_re_register(client, web_app, setup):
    _, student, course = setup
    db.session.commit()
    _faculty_login(client)
    resp = client.post(f"/faculty/courses/{course.id}/students/stu001/face-reset", data={"reason": "Keeps failing"})
    assert resp.status_code == 302 and resp.headers["Location"] == f"/faculty/courses/{course.id}"
    assert b"Face registration reset for Asha Nair" in client.get(resp.headers["Location"]).data

    student_client = web_app.test_client()
    student_client.post("/login", data={"username": "stu001", "password": TEST_PASSWORD})
    assert student_client.get("/student/").headers["Location"] == "/face/enroll"


def test_faculty_reset_without_reason_shows_message(client, setup):
    _, student, course = setup
    db.session.commit()
    _faculty_login(client)
    resp = client.post(f"/faculty/courses/{course.id}/students/stu001/face-reset", data={"reason": " "})
    assert b"Say why the face is being reset" in client.get(resp.headers["Location"]).data
    db.session.expire_all()
    assert FaceEmbedding.query.filter_by(student_id=student.id).count() == 1


def test_faculty_cannot_reset_student_outside_course(client, setup):
    _, _, course = setup
    stranger = make_user(role=Role.STUDENT, username="stranger")
    _give_face(stranger)
    db.session.commit()
    _faculty_login(client)
    assert client.post(f"/faculty/courses/{course.id}/students/stranger/face-reset", data={"reason": "abc"}).status_code == 404
"""Module 5, part 3: attendance results and corrections through the admin API."""
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import AttendanceAudit, AttendanceRecord, Role
from app.services import session_service
from app.utils.security import generate_api_token
from app.utils.timeutils import local_date
from tests.factories import assign_faculty, enroll, make_course, make_record, make_user


@pytest.fixture
def api(client):
    token, token_hash = generate_api_token()
    make_user(role=Role.ADMIN, username="admin1", api_token_hash=token_hash)
    db.session.commit()
    headers = {"Authorization": f"Bearer {token}"}

    class Api:
        def get(self, path):
            return client.get(f"/api/admin{path}", headers=headers)

        def post(self, path, json=None):
            return client.post(f"/api/admin{path}", json=json, headers=headers)

    return Api()


@pytest.fixture
def finished():
    faculty = make_user(role=Role.FACULTY, username="fac001")
    course = make_course(code="DS301")
    assign_faculty(course, faculty)
    students = [make_user(role=Role.STUDENT, username=f"stu{i:03d}", full_name=f"Student {i}") for i in range(3)]
    for s in students:
        enroll(course, s)
    db.session.commit()
    session, _ = session_service.start_session(faculty, course.id)
    make_record(session=session, student=students[0], status="present", method="qr_face", face_distance=0.2)
    db.session.commit()
    session_service.stop_session(faculty, session.id)
    return course, students, session


def test_list_course_sessions_with_counts(api, finished):
    course, _, session = finished
    body = api.get(f"/courses/{course.id}/sessions").get_json()
    assert body["course"]["code"] == "DS301" and body["total"] == 1
    item = body["items"][0]
    assert (item["id"], item["kind"], item["status"], item["present"], item["absent"]) == (session.id, "live", "closed", 1, 2)
    assert item["created_by"] == "fac001" and item["session_date"] == session.session_date.isoformat()


def test_session_detail_has_counts_and_every_student(api, finished):
    _, _, session = finished
    body = api.get(f"/sessions/{session.id}").get_json()
    assert body["counts"] == {"expected": 3, "present": 1, "not_yet": 0, "absent": 2}
    rows = {r["username"]: r for r in body["students"]}
    assert (rows["stu000"]["status"], rows["stu000"]["method"], rows["stu000"]["face_distance"]) == ("present", "qr_face", 0.2)
    assert (rows["stu001"]["status"], rows["stu001"]["method"]) == ("absent", "auto_absent")


def test_change_several_with_reason_and_read_history(api, finished):
    _, _, session = finished
    resp = api.post(f"/sessions/{session.id}/marks",
                    json={"changes": {"STU001": "present", "stu000": "present"}, "reason": "Verified with class register"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["summary"] == {"changed": ["stu001"], "created": [], "unchanged": ["stu000"]}
    assert body["counts"]["present"] == 2

    history = api.get(f"/sessions/{session.id}/students/stu001/history").get_json()
    assert (history["status"], history["method"]) == ("present", "manual")
    entry = history["history"][0]
    assert (entry["old_status"], entry["new_status"], entry["reason"], entry["by"]) == ("absent", "present", "Verified with class register", "admin1")


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"changes": {"stu001": "present"}}, "reason"),
        ({"changes": {"stu001": "late"}, "reason": "Valid reason"}, "changes"),
        ({"changes": {}, "reason": "Valid reason"}, "changes"),
        ({"changes": {"bad name": "present"}, "reason": "Valid reason"}, "changes"),
        ({"changes": {"stu001": "present"}, "reason": "ab"}, "reason"),
        ({"changes": {"stu001": "present"}, "reason": "Valid", "extra": 1}, "extra"),
    ],
)
def test_change_validation(api, finished, payload, field):
    _, _, session = finished
    resp = api.post(f"/sessions/{session.id}/marks", json=payload)
    assert resp.status_code == 422 and field in resp.get_json()["details"]
    assert AttendanceAudit.query.count() == 0


def test_unknown_student_lists_problems_and_changes_nothing(api, finished):
    _, students, session = finished
    resp = api.post(f"/sessions/{session.id}/marks", json={"changes": {"stu001": "present", "ghost01": "present"}, "reason": "Valid reason"})
    assert resp.status_code == 422 and list(resp.get_json()["details"]["changes"]) == ["ghost01"]
    db.session.expire_all()
    assert AttendanceRecord.query.filter_by(session_id=session.id, student_id=students[1].id).one().status == "absent"


def test_record_another_date(api, finished):
    course, _, _ = finished
    past = (local_date() - timedelta(days=5)).isoformat()
    resp = api.post(f"/courses/{course.id}/sessions/manual",
                    json={"session_date": past, "present": ["STU002"], "reason": "Roll taken on paper"})
    body = resp.get_json()
    assert resp.status_code == 201
    assert (body["session"]["kind"], body["session"]["session_date"]) == ("manual", past)
    assert body["counts"] == {"expected": 3, "present": 1, "not_yet": 0, "absent": 2}


def test_another_date_rules(api, finished):
    course, _, session = finished
    url = f"/courses/{course.id}/sessions/manual"
    future = (local_date() + timedelta(days=1)).isoformat()
    assert api.post(url, json={"session_date": future, "reason": "Future class"}).status_code == 422
    assert api.post(url, json={"session_date": "03-11-2026", "reason": "Wrong format"}).status_code == 422
    same_day = {"session_date": session.session_date.isoformat(), "reason": "Second lecture"}
    assert api.post(url, json=same_day).status_code == 409
    assert api.post(url, json={**same_day, "allow_same_day": True}).status_code == 201
    unknown = api.post(url, json={"session_date": "2026-11-02", "present": ["ghost01"], "reason": "Paper roll"})
    assert unknown.status_code == 422 and "present" in unknown.get_json()["details"]


def test_not_found_cases(api, finished):
    _, _, session = finished
    assert api.get("/sessions/99999").status_code == 404
    assert api.get("/courses/99999/sessions").status_code == 404
    assert api.get(f"/sessions/{session.id}/students/nobody99/history").status_code == 404
    assert api.get(f"/sessions/{session.id}/students/bad%20name/history").status_code == 404


def test_live_session_roster_shows_not_yet(api):
    faculty = make_user(role=Role.FACULTY)
    course = make_course()
    assign_faculty(course, faculty)
    enroll(course, make_user(role=Role.STUDENT, username="live001"))
    db.session.commit()
    session, _ = session_service.start_session(faculty, course.id)
    row = api.get(f"/sessions/{session.id}").get_json()["students"][0]
    assert (row["username"], row["status"], row["method"]) == ("live001", None, None)
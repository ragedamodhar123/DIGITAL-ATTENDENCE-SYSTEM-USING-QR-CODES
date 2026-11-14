"""Module 2, part 3: admin JSON API over HTTP."""
import pytest

from app.extensions import db
from app.models import Role
from app.utils.security import generate_api_token
from tests.factories import make_course, make_section, make_user


@pytest.fixture
def token():
    token, token_hash = generate_api_token()
    make_user(role=Role.ADMIN, username="admin1", api_token_hash=token_hash)
    db.session.commit()
    return token


@pytest.fixture
def api(client, token):
    """Small wrapper: api.post("/students", {...}) -> response, with the admin token."""

    class Api:
        def __init__(self):
            self.headers = {"Authorization": f"Bearer {token}"}

        def call(self, method, path, json=None, **kw):
            return client.open(f"/api/admin{path}", method=method, json=json, headers=self.headers, **kw)

        def get(self, path, **kw):
            return self.call("GET", path, **kw)

        def post(self, path, json=None, **kw):
            return self.call("POST", path, json, **kw)

        def patch(self, path, json=None):
            return self.call("PATCH", path, json)

        def delete(self, path):
            return self.call("DELETE", path)

    return Api()


def _seed_section(code="CSE-A"):
    section = make_section(code=code)
    db.session.commit()
    return section


# ---- every admin route needs the token -----------------------------------------

def test_every_admin_route_rejects_requests_without_token(web_app, client):
    rules = [r for r in web_app.url_map.iter_rules() if r.rule.startswith("/api/admin")]
    assert len(rules) >= 20
    for rule in rules:
        path = rule.rule.replace("<int:user_id>", "1").replace("<int:section_id>", "1")
        path = path.replace("<int:course_id>", "1").replace("<int:session_id>", "1").replace("<username>", "someone")
        assert "<" not in path, f"unhandled placeholder in {rule.rule}"
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            resp = client.open(path, method=method, json={})
            assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"
            assert resp.get_json()["error"] == "invalid_token"


def test_logged_in_faculty_cookie_is_not_enough(client):
    make_user(role=Role.FACULTY, username="fac001", must_change_password=False)
    db.session.commit()
    client.post("/login", data={"username": "fac001", "password": "Correct-Horse-9"})
    assert client.get("/api/admin/users").status_code == 401


def test_api_works_with_csrf_protection_switched_on(web_app, api):
    _seed_section()
    web_app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = api.post("/students", {"username": "cs001", "full_name": "A", "section_code": "CSE-A"})
    finally:
        web_app.config["WTF_CSRF_ENABLED"] = False
    assert resp.status_code == 201


def test_api_responses_are_never_cached(api):
    _seed_section()
    resp = api.post("/students", {"username": "cs001", "full_name": "A", "section_code": "CSE-A"})
    assert "no-store" in resp.headers["Cache-Control"]


# ---- users -----------------------------------------------------------------

def test_create_student_returns_user_and_one_time_password(api, client):
    _seed_section()
    resp = api.post("/students", {"username": "CS2024-001", "full_name": "Asha", "section_code": "cse-a"})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["user"]["username"] == "cs2024-001"
    assert data["user"]["must_change_password"] is True
    assert "password_hash" not in data["user"]
    # The student can log in with it straight away.
    login = client.post("/login", data={"username": "cs2024-001", "password": data["temporary_password"]})
    assert login.headers["Location"] == "/change-password"


def test_create_faculty(api):
    resp = api.post("/faculty", {"username": "fac001", "full_name": "Dr Rao", "email": "rao@college.edu"})
    assert resp.status_code == 201 and resp.get_json()["user"]["role"] == "faculty"


def test_validation_errors_are_field_by_field(api):
    resp = api.post("/students", {"username": "bad name", "fullname": "typo"})
    assert resp.status_code == 422
    details = resp.get_json()["details"]
    assert {"username", "fullname", "full_name", "section_code"} <= set(details)


@pytest.mark.parametrize("payload", [None, "not json"])
def test_missing_or_non_json_body(api, payload):
    if payload is None:
        resp = api.post("/faculty")
    else:
        resp = api.call("POST", "/faculty", data=payload, content_type="text/plain")
    assert resp.status_code == 422
    assert resp.get_json()["details"]["_body"]


def test_duplicate_is_409(api):
    api.post("/faculty", {"username": "fac001", "full_name": "A"})
    resp = api.post("/faculty", {"username": "FAC001", "full_name": "B"})
    assert resp.status_code == 409 and "username" in resp.get_json()["details"]


def test_bulk_create_and_all_or_nothing(api):
    _seed_section()
    ok = api.post(
        "/students/bulk",
        {"students": [{"username": f"cs{i:03d}", "full_name": f"S{i}", "section_code": "CSE-A"} for i in range(3)]},
    )
    assert ok.status_code == 201 and ok.get_json()["count"] == 3
    bad = api.post(
        "/students/bulk",
        {"students": [{"username": "cs100", "full_name": "New", "section_code": "CSE-A"},
                      {"username": "cs001", "full_name": "Dup", "section_code": "CSE-A"}]},
    )
    assert bad.status_code == 422 and "1" in bad.get_json()["details"]["students"]
    assert api.get("/users?q=cs100").get_json()["total"] == 0


def test_list_get_update_deactivate_activate(api):
    _seed_section()
    user_id = api.post("/students", {"username": "cs001", "full_name": "A", "section_code": "CSE-A"}).get_json()["user"]["id"]
    api.post("/faculty", {"username": "fac001", "full_name": "F"})

    listing = api.get("/users?role=student&per_page=1&page=1").get_json()
    assert listing["total"] == 1 and listing["page"] == 1 and listing["per_page"] == 1

    assert api.get(f"/users/{user_id}").get_json()["user"]["username"] == "cs001"
    assert api.patch(f"/users/{user_id}", {"full_name": "Asha N"}).get_json()["user"]["full_name"] == "Asha N"
    assert api.post(f"/users/{user_id}/deactivate").get_json()["user"]["is_active"] is False
    assert api.post(f"/users/{user_id}/activate").get_json()["user"]["is_active"] is True


def test_issue_temporary_password_via_api(api):
    resp = api.post("/faculty", {"username": "fac001", "full_name": "F"})
    user_id = resp.get_json()["user"]["id"]
    again = api.post(f"/users/{user_id}/temporary-password")
    assert again.status_code == 200
    assert again.get_json()["temporary_password"] != resp.get_json()["temporary_password"]


def test_unknown_ids_and_admin_ids_are_404_json(api):
    assert api.get("/users/99999").status_code == 404
    admin_id = 1  # the admin created by the token fixture
    assert api.get(f"/users/{admin_id}").status_code == 404
    assert api.get("/users/not-a-number").status_code == 404
    assert api.get("/users/99999").is_json


# ---- sections and courses --------------------------------------------------

def test_section_lifecycle(api):
    created = api.post("/sections", {"code": "cse-a", "name": "CSE A"})
    assert created.status_code == 201
    section_id = created.get_json()["section"]["id"]
    assert api.patch(f"/sections/{section_id}", {"name": "CSE A 2024"}).get_json()["section"]["name"] == "CSE A 2024"
    assert api.post(f"/sections/{section_id}/retire").get_json()["section"]["is_active"] is False
    assert api.get("/sections?active=false").get_json()["total"] == 1
    assert api.post(f"/sections/{section_id}/restore").get_json()["section"]["is_active"] is True


def test_course_lifecycle_assignment_and_enrolment(api):
    _seed_section()
    for i in range(3):
        api.post("/students", {"username": f"cs{i:03d}", "full_name": f"S{i}", "section_code": "CSE-A"})
    api.post("/faculty", {"username": "fac001", "full_name": "F"})

    course = api.post("/courses", {"code": "ds301", "name": "Data Structures", "term": "2026-odd"}).get_json()["course"]
    cid = course["id"]
    assert (course["code"], course["term"]) == ("DS301", "2026-ODD")

    first = api.post(f"/courses/{cid}/faculty", {"username": "fac001"})
    again = api.post(f"/courses/{cid}/faculty", {"username": "fac001"})
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.get_json()["course"]["faculty"] == ["fac001"]

    by_section = api.post(f"/courses/{cid}/enrolments/section", {"section_code": "CSE-A"}).get_json()
    assert len(by_section["summary"]["enrolled"]) == 3 and by_section["course"]["student_count"] == 3

    dropped = api.delete(f"/courses/{cid}/enrolments/cs001").get_json()
    assert dropped["course"]["student_count"] == 2
    roster = api.get(f"/courses/{cid}/roster?include_dropped=true").get_json()["students"]
    assert [(s["username"], s["is_active"]) for s in roster] == [("cs000", True), ("cs001", False), ("cs002", True)]

    back = api.post(f"/courses/{cid}/enrolments", {"usernames": ["cs001"]}).get_json()
    assert back["summary"]["re_enrolled"] == ["cs001"]

    assert api.delete(f"/courses/{cid}/faculty/fac001").get_json()["course"]["faculty"] == []
    assert api.delete(f"/courses/{cid}/faculty/fac001").status_code == 404
    assert api.delete(f"/courses/{cid}/faculty/bad%20name").status_code == 404

    assert api.post(f"/courses/{cid}/retire").get_json()["course"]["is_active"] is False
    assert api.post(f"/courses/{cid}/enrolments", {"usernames": ["cs001"]}).status_code == 422
    listing = api.get("/courses?term=2026-ODD&active=false").get_json()
    assert listing["total"] == 1


def test_enrol_errors_listed_per_username(api):
    course = make_course()
    db.session.commit()
    resp = api.post(f"/courses/{course.id}/enrolments", {"usernames": ["nobody1", "nobody2"]})
    assert resp.status_code == 422
    assert set(resp.get_json()["details"]["usernames"]) == {"nobody1", "nobody2"}
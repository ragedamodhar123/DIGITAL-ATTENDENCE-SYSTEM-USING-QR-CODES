"""Module 6, part 3: below-threshold list (faculty page) and reports in the admin API."""
import csv
import io

import pytest
from openpyxl import load_workbook

from app.extensions import db
from app.models import Role
from app.services import report_service
from app.services.errors import ValidationFailed
from app.utils.security import generate_api_token
from tests.factories import TEST_PASSWORD, assign_faculty, enroll, make_course, make_user
from tests.test_reports import term  # noqa: F401  (fixture: stu001 66.7%, stu002 33.3%, stu003 0.0%)


# ---- service -----------------------------------------------------------------

def test_rows_and_below_threshold_lowest_first(term):
    faculty, course, _, _ = term
    report = report_service.build_course_report(faculty, course.id)
    rows = {r["username"]: r for r in report_service.student_rows(report)}
    assert (rows["stu001"]["present"], rows["stu001"]["absent"], rows["stu001"]["classes"], rows["stu001"]["percent"]) == (2, 1, 3, 66.7)
    assert [r["username"] for r in report_service.below_threshold(report)] == ["stu003", "stu002", "stu001"]
    assert [r["username"] for r in report_service.below_threshold(report, threshold=50)] == ["stu003", "stu002"]
    assert report_service.below_threshold(report, threshold=1) == [{**rows["stu003"], "below": True}]


def test_students_without_classes_are_not_listed(term):
    faculty, course, _, _ = term
    newcomer = make_user(role=Role.STUDENT, username="new001")
    enroll(course, newcomer)
    report = report_service.build_course_report(faculty, course.id)
    assert "new001" not in [r["username"] for r in report_service.below_threshold(report, threshold=100)]


@pytest.mark.parametrize("raw,expected", [(None, 75), ("", 75), ("60", 60), (90, 90), ("100", 100), ("1", 1)])
def test_threshold_values(app, raw, expected):
    assert report_service.valid_threshold(raw) == expected


@pytest.mark.parametrize("raw", ["0", "101", "abc", "7.5", -5])
def test_bad_thresholds(app, raw):
    with pytest.raises(ValidationFailed):
        report_service.valid_threshold(raw)


# ---- faculty page ------------------------------------------------------------

def _login(client, username="fac001"):
    client.post("/login", data={"username": username, "password": TEST_PASSWORD})


def test_course_page_links_to_the_list(client, term):
    _, course, _, _ = term
    _login(client)
    page = client.get(f"/faculty/courses/{course.id}").data.decode()
    assert f"/faculty/courses/{course.id}/below-threshold" in page and "Show students below 75%" in page


def test_list_page(client, term):
    _, course, _, _ = term
    _login(client)
    page = client.get(f"/faculty/courses/{course.id}/below-threshold").data.decode()
    assert "3</strong> of 3 students below 75%" in page
    assert page.index("Chitra Das") < page.index("Bala Kumar") < page.index("Asha Nair")  # lowest first
    assert "missed 2 of 2" in page and "33.3%" in page


def test_list_page_with_threshold_and_dates(client, term):
    _, course, _, _ = term
    _login(client)
    page = client.get(f"/faculty/courses/{course.id}/below-threshold?threshold=40&from=2026-11-03&to=2026-11-04").data.decode()
    # 3-4 Nov: stu001 1/2 = 50% (not below 40), stu002 0/2, stu003 0/2
    assert "Students below 40%" in page and "2</strong> of 3" in page and "Asha Nair" not in page


def test_nobody_below(client, term):
    _, course, _, _ = term
    _login(client)
    assert "Nobody is below 1%" in client.get(f"/faculty/courses/{course.id}/below-threshold?threshold=1&from=2026-11-02&to=2026-11-02").data.decode()


@pytest.mark.parametrize("query,message", [("threshold=500", "whole number from 1 to 100"), ("from=bad", "Choose valid dates.")])
def test_list_page_bad_input(client, term, query, message):
    _, course, _, _ = term
    _login(client)
    resp = client.get(f"/faculty/courses/{course.id}/below-threshold?{query}")
    assert resp.status_code == 302 and message in client.get(resp.headers["Location"]).data.decode()


def test_list_page_only_for_own_courses(client, term):
    _, course, _, _ = term
    make_user(role=Role.FACULTY, username="fac999", must_change_password=False)
    db.session.commit()
    _login(client, "fac999")
    assert client.get(f"/faculty/courses/{course.id}/below-threshold").status_code == 403


# ---- admin API ---------------------------------------------------------------

@pytest.fixture
def headers():
    token, token_hash = generate_api_token()
    make_user(role=Role.ADMIN, username="admin1", api_token_hash=token_hash)
    db.session.commit()
    return {"Authorization": f"Bearer {token}"}


def test_api_json_report(client, term, headers):
    _, course, _, sessions = term
    body = client.get(f"/api/admin/courses/{course.id}/report", headers=headers).get_json()
    assert body["course"]["code"] == "DS301" and body["threshold"] == 75
    assert [c["session_id"] for c in body["classes"]] == [s.id for s in sessions]
    stu1 = next(s for s in body["students"] if s["username"] == "stu001")
    assert (stu1["present"], stu1["classes"], stu1["percent"], stu1["below"]) == (2, 3, 66.7, True)


def test_api_file_downloads_match_faculty_files(client, term, headers):
    _, course, _, _ = term
    xlsx = client.get(f"/api/admin/courses/{course.id}/report?format=xlsx&from=2026-11-02", headers=headers)
    assert xlsx.status_code == 200 and "attachment" in xlsx.headers["Content-Disposition"]
    assert load_workbook(io.BytesIO(xlsx.data))["Attendance"]["G8"].value == '=COUNTIF(D8:F8,"P")'
    csv_resp = client.get(f"/api/admin/courses/{course.id}/report?format=csv", headers=headers)
    rows = list(csv.reader(io.StringIO(csv_resp.data.decode("utf-8-sig"))))
    assert rows[1][-3:] == ["2", "3", "66.7"]


def test_api_below_threshold(client, term, headers):
    _, course, _, _ = term
    body = client.get(f"/api/admin/courses/{course.id}/below-threshold?threshold=50", headers=headers).get_json()
    assert body["count"] == 2 and [s["username"] for s in body["students"]] == ["stu003", "stu002"]


@pytest.mark.parametrize("query,field", [
    ("format=pdf", "format"), ("threshold=0", "threshold"), ("from=03-11-2026", "from"), ("threshold=abc", "threshold"),
])
def test_api_bad_queries(client, term, headers, query, field):
    _, course, _, _ = term
    resp = client.get(f"/api/admin/courses/{course.id}/report?{query}", headers=headers)
    assert resp.status_code == 422 and field in resp.get_json()["details"]


def test_api_reversed_dates_and_unknown_course(client, term, headers):
    _, course, _, _ = term
    assert client.get(f"/api/admin/courses/{course.id}/report?from=2026-11-05&to=2026-11-01", headers=headers).status_code == 422
    assert client.get("/api/admin/courses/99999/below-threshold", headers=headers).status_code == 404


def test_api_needs_token(client, term):
    _, course, _, _ = term
    assert client.get(f"/api/admin/courses/{course.id}/report").status_code == 401
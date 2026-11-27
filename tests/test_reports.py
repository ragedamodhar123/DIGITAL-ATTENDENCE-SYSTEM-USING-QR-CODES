"""Module 6, part 2: course attendance reports (Excel and CSV)."""
import csv
import io
from datetime import date, datetime, timedelta, timezone

import pytest
from openpyxl import load_workbook

from app.extensions import db
from app.models import Enrollment, Role
from app.services import manual_service, report_service, session_service
from app.services.errors import NotFound, PermissionDenied, ValidationFailed
from tests.factories import TEST_PASSWORD, assign_faculty, enroll, make_course, make_record, make_user

T0 = datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc)  # Mon 2 Nov 2026, 09:30 in India


@pytest.fixture
def term():
    """DS301, 3 finished classes on 2, 3 and 4 Nov. stu001 P P A, stu002 P A A; stu003 joins after class 1."""
    faculty = make_user(role=Role.FACULTY, username="fac001", must_change_password=False)
    course = make_course(code="DS301", name="Data Structures", term="2026-ODD")
    assign_faculty(course, faculty)
    s1 = make_user(role=Role.STUDENT, username="stu001", full_name="Asha Nair")
    s2 = make_user(role=Role.STUDENT, username="stu002", full_name="Bala Kumar")
    s3 = make_user(role=Role.STUDENT, username="stu003", full_name="Chitra Das")
    enroll(course, s1)
    enroll(course, s2)
    marks = {0: {s1: "present", s2: "present"}, 1: {s1: "present"}, 2: {}}
    sessions = []
    for day in range(3):
        if day == 1:
            enroll(course, s3)
        start = T0 + timedelta(days=day)
        session, _ = session_service.start_session(faculty, course.id, now=start)
        for student, status in marks[day].items():
            make_record(session=session, student=student, status=status, method="qr_face")
        session_service.stop_session(faculty, session.id, now=start + timedelta(minutes=50))
        sessions.append(session)
    db.session.commit()
    return faculty, course, (s1, s2, s3), sessions


def _grid(report):
    return {u.username: [report.grid.at[u.id, s.id] for s in report.sessions] for u in report.students}


# ---- data ----------------------------------------------------------------------

def test_grid_and_totals(term):
    faculty, course, (s1, s2, s3), _ = term
    report = report_service.build_course_report(faculty, course.id)
    assert _grid(report) == {"stu001": ["P", "P", "A"], "stu002": ["P", "A", "A"], "stu003": ["", "A", "A"]}
    assert report.totals(s1.id) == (2, 3, 66.7)
    assert report.totals(s2.id) == (1, 3, 33.3)
    assert report.totals(s3.id) == (0, 2, 0.0)  # not counted before joining


def test_date_range(term):
    faculty, course, (s1, _, _), _ = term
    report = report_service.build_course_report(faculty, course.id, date(2026, 11, 3), date(2026, 11, 4))
    assert len(report.sessions) == 2 and report.totals(s1.id) == (1, 2, 50.0)


def test_running_class_not_included(term):
    faculty, course, _, sessions = term
    session_service.start_session(faculty, course.id)
    assert [s.id for s in report_service.build_course_report(faculty, course.id).sessions] == [s.id for s in sessions]


def test_dropped_student_keeps_history_and_manual_class_is_labelled(term):
    faculty, course, (s1, s2, _), _ = term
    Enrollment.query.filter_by(student_id=s2.id).one().is_active = False
    manual_service.create_manual_session(faculty, course.id, date(2026, 10, 30), ["stu001"], "Roll on paper", now=T0)
    report = report_service.build_course_report(faculty, course.id)
    assert "stu002" in _grid(report)
    assert report.column_label(report.sessions[0]).endswith("(by hand)")
    assert _grid(report)["stu002"][0] == ""  # dropped before the paper roll was taken


def test_no_classes_yet():
    faculty = make_user(role=Role.FACULTY)
    course = make_course()
    assign_faculty(course, faculty)
    enroll(course, make_user(role=Role.STUDENT))
    report = report_service.build_course_report(faculty, course.id)
    assert report.sessions == [] and len(report.students) == 1
    assert report.totals(report.students[0].id) == (0, 0, None)
    report_service.to_xlsx(report)
    report_service.to_csv(report)


def test_permissions_and_bad_input(term):
    _, course, (s1, _, _), _ = term
    admin = make_user(role=Role.ADMIN)
    report_service.build_course_report(admin, course.id)  # admin may
    for actor in (make_user(role=Role.FACULTY), s1, None):
        with pytest.raises(PermissionDenied):
            report_service.build_course_report(actor, course.id)
    with pytest.raises(NotFound):
        report_service.build_course_report(admin, 99999)
    with pytest.raises(ValidationFailed):
        report_service.build_course_report(admin, course.id, date(2026, 11, 5), date(2026, 11, 1))


# ---- CSV -----------------------------------------------------------------------

def test_csv_content(term):
    faculty, course, _, _ = term
    data = report_service.to_csv(report_service.build_course_report(faculty, course.id))
    assert data.startswith("\ufeff".encode("utf-8"))  # Excel reads UTF-8 names correctly
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert rows[0][:3] == ["Roll number", "Name", "Section"] and rows[0][-3:] == ["Present", "Classes", "Attendance %"]
    assert rows[0][3] == "02-Nov-2026 09:30"
    assert rows[1][0] == "STU001" and rows[1][3:] == ["P", "P", "A", "2", "3", "66.7"]
    assert rows[3][3:] == ["", "A", "A", "0", "2", "0.0"]


# ---- Excel ---------------------------------------------------------------------

def test_excel_layout_and_formulas(term):
    faculty, course, _, _ = term
    sheet = load_workbook(io.BytesIO(report_service.to_xlsx(report_service.build_course_report(faculty, course.id))))["Attendance"]
    assert sheet["A1"].value == "DS301 Data Structures"
    assert (sheet["A5"].value, sheet["B5"].value, sheet["B5"].number_format) == ("Highlight below", 0.75, "0%")
    assert [sheet.cell(row=7, column=c).value for c in range(1, 10)] == [
        "Roll number", "Name", "Section", "02-Nov-2026 09:30", "03-Nov-2026 09:30", "04-Nov-2026 09:30",
        "Present", "Classes", "Attendance %"]
    assert [sheet.cell(row=8, column=c).value for c in range(1, 7)] == ["STU001", "Asha Nair", sheet["C8"].value, "P", "P", "A"]
    assert sheet["G8"].value == '=COUNTIF(D8:F8,"P")'
    assert sheet["H8"].value == '=COUNTIF(D8:F8,"P")+COUNTIF(D8:F8,"A")'
    assert sheet["I8"].value == '=IF(H8=0,"",G8/H8)' and sheet["I8"].number_format == "0.0%"
    assert sheet["D11"].value == '=COUNTIF(D8:D10,"P")'  # per-class totals row
    assert sheet.freeze_panes == "D8"
    rules = [r.formula[0] for rng in sheet.conditional_formatting for r in rng.rules]
    assert any("$B$5" in f for f in rules)  # highlighting follows the editable threshold cell
    assert all(cell.font.name == "Arial" for cell in sheet[8])


def test_names_that_look_like_formulas_stay_text(term):
    # Spreadsheet formula injection: such a name must never become a live formula.
    faculty, course, (s1, _, _), _ = term
    s1.full_name = '=HYPERLINK("http://evil.example","Click")'
    db.session.commit()
    report = report_service.build_course_report(faculty, course.id)
    cell = load_workbook(io.BytesIO(report_service.to_xlsx(report)))["Attendance"]["B8"]
    assert cell.data_type == "s" and cell.value == s1.full_name
    rows = list(csv.reader(io.StringIO(report_service.to_csv(report).decode("utf-8-sig"))))
    assert rows[1][1].startswith("'=HYPERLINK")


def test_file_names(term):
    faculty, course, _, _ = term
    report = report_service.build_course_report(faculty, course.id, date(2026, 11, 1), None)
    assert report_service.file_name(report, "xlsx") == "DS301_2026-ODD_attendance_2026-11-01_to_today.xlsx"


# ---- download route ------------------------------------------------------------

def _login(client):
    client.post("/login", data={"username": "fac001", "password": TEST_PASSWORD})


def test_course_page_has_report_form(client, term):
    _, course, _, _ = term
    _login(client)
    page = client.get(f"/faculty/courses/{course.id}").data.decode()
    assert 'id="report"' in page and 'value="xlsx"' in page and 'value="csv"' in page


@pytest.mark.parametrize("fmt,mimetype,ext", [("xlsx", "spreadsheetml", ".xlsx"), ("csv", "text/csv", ".csv")])
def test_download(client, term, fmt, mimetype, ext):
    _, course, _, _ = term
    _login(client)
    resp = client.get(f"/faculty/courses/{course.id}/report?format={fmt}&from=2026-11-02&to=")
    assert resp.status_code == 200 and mimetype in resp.headers["Content-Type"]
    disposition = resp.headers["Content-Disposition"]
    assert "attachment" in disposition and ext in disposition and "2026-11-02_to_today" in disposition
    assert len(resp.data) > 100


@pytest.mark.parametrize("query,message", [
    ("format=pdf", "Choose Excel or CSV."),
    ("format=xlsx&from=not-a-date", "Choose valid dates."),
    ("format=xlsx&from=2026-11-05&to=2026-11-01", "end date must be on or after"),
])
def test_download_problems_are_explained(client, term, query, message):
    _, course, _, _ = term
    _login(client)
    resp = client.get(f"/faculty/courses/{course.id}/report?{query}")
    assert resp.status_code == 302
    assert message in client.get(resp.headers["Location"]).data.decode()


def test_other_faculty_cannot_download(client, term):
    _, course, _, _ = term
    make_user(role=Role.FACULTY, username="fac999", must_change_password=False)
    db.session.commit()
    client.post("/login", data={"username": "fac999", "password": TEST_PASSWORD})
    assert client.get(f"/faculty/courses/{course.id}/report?format=csv").status_code == 403
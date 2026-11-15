"""Attendance reports for a course: a grid of students x classes, as Excel or CSV.

Who: the admin, or a faculty member assigned to the course.
What: finished classes (closed sessions) in an optional date range, oldest first.
Cells: "P" present, "A" absent, blank = the student had no record for that class
(for example, not yet enrolled). Per student: Present, Classes, Attendance %.

Excel: totals are real formulas (COUNTIF), so correcting a P/A in the file updates
the numbers; the warning threshold sits in its own labelled cell, and rows below it
are highlighted by conditional formatting that also follows that cell.
CSV: the same grid with the totals as plain numbers (CSV cannot hold formulas).

Security: names and codes are always stored as text. In Excel the cells are typed as
text; in CSV, text starting like a formula (= + - @) gets a leading apostrophe. So a
student name such as "=HYPERLINK(...)" can never run when the file is opened.
"""
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
from flask import current_app
from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..extensions import db
from ..models import AttendanceRecord, AttendanceSession, Course, Enrollment, SessionStatus, User
from ..utils.timeutils import to_local, utcnow
from .common import valid_id
from .errors import NotFound, PermissionDenied, ValidationFailed
from .manual_service import can_edit
from .session_service import close_expired_sessions

MARK = {"present": "P", "absent": "A"}
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
FONT = "Arial"


@dataclass
class CourseReport:
    course: Course
    date_from: date = None
    date_to: date = None
    sessions: list = field(default_factory=list)   # AttendanceSession, oldest first
    grid: pd.DataFrame = None                      # index: student id; columns: session id; values "P"/"A"/""
    students: list = field(default_factory=list)   # User, ordered by username
    generated_at: object = None

    def column_label(self, session):
        local = to_local(session.started_at)
        label = f"{session.session_date.strftime('%d-%b-%Y')} {local.strftime('%H:%M')}"
        return label + (" (by hand)" if session.kind == "manual" else "")

    def totals(self, student_id):
        """(present, classes, percent or None) computed in Python (CSV, tests, screens)."""
        row = self.grid.loc[student_id] if student_id in self.grid.index else pd.Series(dtype=object)
        present = int((row == "P").sum())
        classes = present + int((row == "A").sum())
        return present, classes, (round(100.0 * present / classes, 1) if classes else None)


def safe_text(value):
    """Stop spreadsheet formula injection: text starting like a formula gets a leading apostrophe."""
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        return "'" + value
    return value


# ---- building the data -------------------------------------------------------

def build_course_report(actor, course_id, date_from=None, date_to=None):
    course = db.session.get(Course, course_id) if valid_id(course_id) else None
    if course is None:
        raise NotFound("Course not found.")
    if not can_edit(actor, course.id):
        raise PermissionDenied("You can only download reports for courses you teach.")
    if date_from and date_to and date_from > date_to:
        raise ValidationFailed("Dates are the wrong way round.", details={"date_to": ["The end date must be on or after the start date."]})
    close_expired_sessions()

    query = AttendanceSession.query.filter(
        AttendanceSession.course_id == course.id, AttendanceSession.status == SessionStatus.CLOSED
    )
    if date_from:
        query = query.filter(AttendanceSession.session_date >= date_from)
    if date_to:
        query = query.filter(AttendanceSession.session_date <= date_to)
    sessions = query.order_by(AttendanceSession.session_date, AttendanceSession.started_at, AttendanceSession.id).all()
    session_ids = [s.id for s in sessions]

    records = (
        db.session.query(AttendanceRecord.student_id, AttendanceRecord.session_id, AttendanceRecord.status)
        .filter(AttendanceRecord.session_id.in_(session_ids))
        .all()
        if session_ids else []
    )
    # Students: everyone with a record in these classes, plus everyone actively enrolled now.
    active_ids = {
        sid for (sid,) in db.session.query(Enrollment.student_id).filter_by(course_id=course.id, is_active=True)
    }
    student_ids = active_ids | {student_id for student_id, _, _ in records}
    students = User.query.filter(User.id.in_(student_ids)).order_by(User.username).all() if student_ids else []

    frame = pd.DataFrame(records, columns=["student_id", "session_id", "status"])
    frame["mark"] = frame["status"].map(MARK)
    grid = (
        frame.pivot(index="student_id", columns="session_id", values="mark")
        if not frame.empty else pd.DataFrame()
    )
    grid = grid.reindex(index=[u.id for u in students], columns=session_ids).fillna("")

    return CourseReport(
        course=course, date_from=date_from, date_to=date_to, sessions=sessions,
        grid=grid, students=students, generated_at=utcnow(),
    )


def file_name(report, extension):
    period = ""
    if report.date_from or report.date_to:
        period = f"_{report.date_from or 'start'}_to_{report.date_to or 'today'}"
    raw = f"{report.course.code}_{report.course.term}_attendance{period}.{extension}"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw)


# ---- CSV ---------------------------------------------------------------------

def to_csv(report):
    """UTF-8 with a byte-order mark, so Excel shows non-English names correctly."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Roll number", "Name", "Section"] + [report.column_label(s) for s in report.sessions]
                    + ["Present", "Classes", "Attendance %"])
    for student in report.students:
        marks = [report.grid.at[student.id, s.id] for s in report.sessions] if report.sessions else []
        present, classes, percent = report.totals(student.id)
        writer.writerow(
            [safe_text(student.username.upper()), safe_text(student.full_name),
             safe_text(student.section.code if student.section else "")]
            + marks + [present, classes, "" if percent is None else percent]
        )
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


# ---- Excel -------------------------------------------------------------------

HEADER_ROW = 7            # column titles
FIRST_DATA_ROW = 8
FIRST_MARK_COL = 4        # D: first class column (A roll, B name, C section)
THRESHOLD_CELL = "$B$5"


def _font(**kw):
    return Font(name=FONT, **kw)


def _text(sheet, row, column, value):
    """Write text that is ALWAYS text: a name like "=HYPERLINK(...)" is stored as a
    plain string (shown exactly as typed) instead of becoming a formula."""
    cell = sheet.cell(row=row, column=column, value=value)
    if isinstance(value, str):
        cell.data_type = "s"
    return cell


def to_xlsx(report):
    config = current_app.config
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Attendance"
    thin = Side(style="thin", color="D9DEE7")
    header_fill = PatternFill("solid", fgColor="1F2A44")

    n_sessions = len(report.sessions)
    n_students = len(report.students)
    present_col = FIRST_MARK_COL + n_sessions
    classes_col, percent_col = present_col + 1, present_col + 2
    last_data_row = FIRST_DATA_ROW + n_students - 1
    first_mark = get_column_letter(FIRST_MARK_COL)
    last_mark = get_column_letter(present_col - 1)

    # Title block, with the threshold as an editable, labelled input.
    _text(sheet, 1, 1, f"{report.course.code} {report.course.name}")
    sheet["A1"].font = _font(bold=True, size=14, color="1F2A44")
    _text(sheet, 2, 1, f"Term {report.course.term}")
    period = "all finished classes"
    if report.date_from or report.date_to:
        period = f"{report.date_from or 'start'} to {report.date_to or 'today'}"
    sheet["A3"] = f"Period: {period}. Generated {to_local(report.generated_at).strftime('%d %b %Y %H:%M')}."
    sheet["A5"] = "Highlight below"
    sheet["B5"] = config["ATTENDANCE_WARNING_PERCENT"] / 100
    sheet["B5"].number_format = "0%"
    sheet["B5"].font = _font(bold=True, color="0000FF")  # blue = an input you may change
    sheet["B5"].alignment = Alignment(horizontal="left")
    sheet["A6"] = ("P = present, A = absent, blank = not enrolled at the time. "
                   "Change the percentage above to highlight a different group; totals update automatically.")
    for cell in ("A2", "A3", "A6"):
        sheet[cell].font = _font(color="5B6475")
    sheet["A5"].font = _font(bold=True)

    headers = ["Roll number", "Name", "Section"] + [report.column_label(s) for s in report.sessions] + [
        "Present", "Classes", "Attendance %"]
    for col, title in enumerate(headers, start=1):
        cell = sheet.cell(row=HEADER_ROW, column=col, value=title)
        cell.font = _font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[HEADER_ROW].height = 32

    for offset, student in enumerate(report.students):
        row = FIRST_DATA_ROW + offset
        _text(sheet, row, 1, student.username.upper())
        _text(sheet, row, 2, student.full_name)
        _text(sheet, row, 3, student.section.code if student.section else "")
        for index, session in enumerate(report.sessions):
            mark = report.grid.at[student.id, session.id]
            cell = sheet.cell(row=row, column=FIRST_MARK_COL + index, value=mark or None)
            cell.alignment = Alignment(horizontal="center")
        if n_sessions:
            marks = f"{first_mark}{row}:{last_mark}{row}"
            sheet.cell(row=row, column=present_col, value=f'=COUNTIF({marks},"P")')
            sheet.cell(row=row, column=classes_col, value=f'=COUNTIF({marks},"P")+COUNTIF({marks},"A")')
        else:
            sheet.cell(row=row, column=present_col, value=0)
            sheet.cell(row=row, column=classes_col, value=0)
        p, c = get_column_letter(present_col), get_column_letter(classes_col)
        percent = sheet.cell(row=row, column=percent_col, value=f'=IF({c}{row}=0,"",{p}{row}/{c}{row})')
        percent.number_format = "0.0%"
        for col in range(1, percent_col + 1):
            sheet.cell(row=row, column=col).font = _font()
            sheet.cell(row=row, column=col).border = Border(bottom=thin)

    # Per-class totals under the grid.
    if n_students and n_sessions:
        total_row = last_data_row + 1
        sheet.cell(row=total_row, column=2, value="Present in class").font = _font(bold=True)
        for index in range(n_sessions):
            letter = get_column_letter(FIRST_MARK_COL + index)
            cell = sheet.cell(row=total_row, column=FIRST_MARK_COL + index,
                              value=f'=COUNTIF({letter}{FIRST_DATA_ROW}:{letter}{last_data_row},"P")')
            cell.font = _font(bold=True)
            cell.alignment = Alignment(horizontal="center")

    if n_students:
        pct = get_column_letter(percent_col)
        whole_rows = f"A{FIRST_DATA_ROW}:{pct}{last_data_row}"
        sheet.conditional_formatting.add(
            whole_rows,
            FormulaRule(formula=[f'AND(${pct}{FIRST_DATA_ROW}<>"",${pct}{FIRST_DATA_ROW}<{THRESHOLD_CELL})'],
                        fill=PatternFill("solid", fgColor="FCE4E4")),
        )
    else:
        sheet.cell(row=FIRST_DATA_ROW, column=1, value="No students in this course.").font = _font(italic=True)
    if not n_sessions:
        sheet["A4"] = "No finished classes in this period."
        sheet["A4"].font = _font(italic=True, color="B3261E")

    widths = {1: 17, 2: 28, 3: 12}
    for col in range(1, percent_col + 1):
        sheet.column_dimensions[get_column_letter(col)].width = widths.get(col, 12)
    sheet.freeze_panes = sheet.cell(row=FIRST_DATA_ROW, column=FIRST_MARK_COL)
    sheet.print_title_rows = f"{HEADER_ROW}:{HEADER_ROW}"
    sheet.page_setup.orientation = "landscape"
    # Print: all columns on one page width (many classes shrink to fit), rows continue on more pages.
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ---- summaries (screens, JSON, below-threshold list) -----------------------------

def student_rows(report, threshold=None):
    """One dict per student with totals, for screens and JSON. Same numbers as the files."""
    if threshold is None:
        threshold = current_app.config["ATTENDANCE_WARNING_PERCENT"]
    rows = []
    for student in report.students:
        present, classes, percent = report.totals(student.id)
        rows.append({
            "username": student.username,
            "full_name": student.full_name,
            "section_code": student.section.code if student.section else None,
            "present": present,
            "absent": classes - present,
            "classes": classes,
            "percent": percent,
            "below": percent is not None and percent < threshold,
        })
    return rows


def below_threshold(report, threshold=None):
    """Students under the threshold, lowest attendance first. Students with no
    finished classes yet are not listed (there is nothing to judge)."""
    rows = [row for row in student_rows(report, threshold) if row["below"]]
    return sorted(rows, key=lambda row: (row["percent"], row["username"]))


def valid_threshold(raw):
    """'' / None -> the configured default; otherwise a whole number 1-100, else ValidationFailed."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return current_app.config["ATTENDANCE_WARNING_PERCENT"]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = None
    if value is None or not 1 <= value <= 100:
        raise ValidationFailed("Invalid threshold.", details={"threshold": ["Use a whole number from 1 to 100."]})
    return value
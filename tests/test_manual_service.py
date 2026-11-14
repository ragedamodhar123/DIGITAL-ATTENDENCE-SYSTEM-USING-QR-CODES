"""Module 5, part 1: manual marking and edits, with reasons and audit history."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.extensions import db
from app.models import AttendanceAudit, AttendanceRecord, AttendanceSession, Enrollment, PendingCheckin, Role
from app.services import checkin_service, manual_service as svc, qr_service, session_service
from app.services.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from tests.factories import assign_faculty, enroll, make_course, make_record, make_user

T0 = datetime(2026, 11, 10, 4, 0, tzinfo=timezone.utc)  # 10 Nov 2026, 09:30 in India
REASON = "Phone battery was dead"


@pytest.fixture
def klass():
    faculty = make_user(role=Role.FACULTY, username="fac001")
    course = make_course(code="DS301")
    assign_faculty(course, faculty)
    students = [make_user(role=Role.STUDENT, username=f"stu{i:03d}") for i in range(3)]
    for s in students:
        enroll(course, s)
    return faculty, course, students


@pytest.fixture
def closed_session(klass):
    """A finished live class: stu000 present by face, stu001 and stu002 auto-absent."""
    faculty, course, students = klass
    session, _ = session_service.start_session(faculty, course.id, now=T0)
    make_record(session=session, student=students[0], status="present", method="qr_face", face_distance=0.21)
    session_service.stop_session(faculty, session.id, now=T0 + timedelta(minutes=50))
    return faculty, course, students, session


def _record(session, student):
    return AttendanceRecord.query.filter_by(session_id=session.id, student_id=student.id).one()


# ---- editing an existing session ---------------------------------------------

def test_change_absent_to_present_is_audited(closed_session):
    faculty, _, students, session = closed_session
    later = T0 + timedelta(days=2)
    summary = svc.set_status(faculty, session.id, "stu001", "present", "  Was in class, phone died  ", now=later)
    assert summary.changed == ["stu001"]

    record = _record(session, students[1])
    assert (record.status, record.method, record.marked_by, record.marked_at) == ("present", "manual", faculty, later)
    audit = AttendanceAudit.query.one()
    assert (audit.old_status, audit.new_status, audit.reason, audit.changed_by) == ("absent", "present", "Was in class, phone died", faculty)
    assert audit.changed_at is not None


def test_change_keeps_face_evidence(closed_session):
    faculty, _, students, session = closed_session
    svc.set_status(faculty, session.id, "stu000", "absent", "Left after roll call")
    record = _record(session, students[0])
    assert (record.status, record.method, record.face_distance) == ("absent", "manual", 0.21)


def test_several_changes_and_unchanged_reported(closed_session):
    faculty, _, _, session = closed_session
    summary = svc.set_statuses(faculty, session.id, {"stu000": "present", "stu001": "present", "stu002": "present"}, REASON)
    assert (summary.changed, summary.unchanged) == (["stu001", "stu002"], ["stu000"])
    assert AttendanceAudit.query.count() == 2  # no audit row where nothing changed


def test_full_history_of_a_record(closed_session):
    faculty, _, _, session = closed_session
    svc.set_status(faculty, session.id, "stu001", "present", "First correction", now=T0 + timedelta(days=1))
    svc.set_status(faculty, session.id, "stu001", "absent", "Checked CCTV, was not there", now=T0 + timedelta(days=2))
    _, history = svc.record_history(faculty, session.id, "stu001")
    assert [(h.old_status, h.new_status, h.reason) for h in history] == [
        ("absent", "present", "First correction"),
        ("present", "absent", "Checked CCTV, was not there"),
    ]


@pytest.mark.parametrize("reason", [None, "", "  ", "ab", "x" * 501])
def test_reason_required(closed_session, reason):
    faculty, _, students, session = closed_session
    with pytest.raises(ValidationFailed) as exc:
        svc.set_status(faculty, session.id, "stu001", "present", reason)
    assert "reason" in exc.value.details
    assert _record(session, students[1]).status == "absent"


@pytest.mark.parametrize("status", ["late", "PRESENT", "", None])
def test_only_present_or_absent(closed_session, status):
    faculty, _, _, session = closed_session
    with pytest.raises(ValidationFailed):
        svc.set_status(faculty, session.id, "stu001", status, REASON)


def test_bad_username_changes_nothing_for_anyone(closed_session):
    faculty, _, students, session = closed_session
    make_user(role=Role.FACULTY, username="fac999")
    with pytest.raises(ValidationFailed) as exc:
        svc.set_statuses(faculty, session.id, {"stu001": "present", "nobody": "present", "fac999": "absent"}, REASON)
    assert set(exc.value.details["changes"]) == {"nobody", "fac999"}
    assert _record(session, students[1]).status == "absent"  # stu001 not changed either
    assert AttendanceAudit.query.count() == 0


def test_dropped_students_old_record_can_still_be_corrected(closed_session):
    faculty, _, students, session = closed_session
    Enrollment.query.filter_by(student_id=students[1].id).one().is_active = False
    students[2].is_active = False
    db.session.flush()
    summary = svc.set_statuses(faculty, session.id, {"stu001": "present", "stu002": "present"}, REASON)
    assert summary.changed == ["stu001", "stu002"]


def test_new_record_only_for_actively_enrolled_students(closed_session):
    faculty, course, _, session = closed_session
    newcomer = make_user(role=Role.STUDENT, username="new001")
    enroll(course, newcomer)  # enrolled after the class: has no record
    summary = svc.set_status(faculty, session.id, "new001", "present", "Joined late, was present")
    assert summary.created == ["new001"]
    assert AttendanceAudit.query.one().old_status is None

    outsider = make_user(role=Role.STUDENT, username="out001")
    with pytest.raises(ValidationFailed):
        svc.set_status(faculty, session.id, outsider.username, "present", REASON)


# ---- during a live class -----------------------------------------------------

def test_mark_present_during_class_after_failed_face_checks(klass):
    faculty, course, students = klass
    session, _ = session_service.start_session(faculty, course.id, now=T0)
    svc.set_status(faculty, session.id, "stu001", "present", "Face check failed 3 times, seen in class", now=T0 + timedelta(minutes=5))
    session_service.stop_session(faculty, session.id, now=T0 + timedelta(minutes=50))
    statuses = {r.student.username: (r.status, r.method) for r in AttendanceRecord.query.filter_by(session_id=session.id)}
    assert statuses == {"stu000": ("absent", "auto_absent"), "stu001": ("present", "manual"), "stu002": ("absent", "auto_absent")}


def test_manual_mark_ends_a_face_check_in_progress(klass):
    from tests.test_attendance_service import register_face

    faculty, course, students = klass
    register_face(students[1])
    session, _ = session_service.start_session(faculty, course.id, now=T0)
    checkin_service.begin_checkin(students[1], qr_service.make_token(session, T0)[0], now=T0)
    svc.set_status(faculty, session.id, "stu001", "absent", "Left the room", now=T0 + timedelta(seconds=5))
    assert PendingCheckin.query.count() == 0


# ---- a date with no live session ---------------------------------------------

def test_manual_session_for_a_past_date(klass):
    faculty, course, _ = klass
    session, summary = svc.create_manual_session(
        faculty, course.id, date(2026, 11, 3), ["stu000", "stu002"], "Projector broken, took roll by hand", now=T0
    )
    assert (session.kind, session.status, session.session_date) == ("manual", "closed", date(2026, 11, 3))
    assert session.qr_secret is None
    statuses = {r.student.username: (r.status, r.method) for r in AttendanceRecord.query.filter_by(session_id=session.id)}
    assert statuses == {"stu000": ("present", "manual"), "stu001": ("absent", "manual"), "stu002": ("present", "manual")}
    assert AttendanceAudit.query.count() == 3 and summary.created == ["stu000", "stu001", "stu002"]


def test_today_is_allowed_but_not_the_future(klass):
    faculty, course, _ = klass
    svc.create_manual_session(faculty, course.id, date(2026, 11, 10), [], REASON, now=T0)
    with pytest.raises(ValidationFailed) as exc:
        svc.create_manual_session(faculty, course.id, date(2026, 11, 11), [], REASON, now=T0)
    assert "future" in exc.value.details["session_date"][0]


def test_future_check_uses_college_date(klass):
    # 20:00 UTC on 10 Nov is already 11 Nov in India, so 11 Nov counts as today.
    faculty, course, _ = klass
    evening_utc = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)
    svc.create_manual_session(faculty, course.id, date(2026, 11, 11), [], REASON, now=evening_utc)


def test_same_date_needs_confirmation(closed_session):
    faculty, course, _, _ = closed_session
    with pytest.raises(Conflict) as exc:
        svc.create_manual_session(faculty, course.id, date(2026, 11, 10), [], REASON, now=T0 + timedelta(hours=3))
    assert "already has 1" in exc.value.details["session_date"][0]
    session, _ = svc.create_manual_session(
        faculty, course.id, date(2026, 11, 10), [], "Second lecture today", allow_same_day=True, now=T0 + timedelta(hours=3)
    )
    assert AttendanceSession.query.filter_by(course_id=course.id, session_date=date(2026, 11, 10)).count() == 2


def test_manual_session_rejects_unknown_present_names(klass):
    faculty, course, _ = klass
    with pytest.raises(ValidationFailed) as exc:
        svc.create_manual_session(faculty, course.id, date(2026, 11, 3), ["stu000", "ghost"], REASON, now=T0)
    assert list(exc.value.details["present"]) == ["ghost"]
    assert AttendanceSession.query.count() == 0


@pytest.mark.parametrize("bad_date", [None, "2026-11-03", date(1999, 12, 31)])
def test_manual_session_bad_dates(klass, bad_date):
    faculty, course, _ = klass
    with pytest.raises(ValidationFailed):
        svc.create_manual_session(faculty, course.id, bad_date, [], REASON, now=T0)


def test_manual_session_needs_students_and_active_course(klass):
    faculty, course, _ = klass
    empty = make_course(code="EMPTY1")
    assign_faculty(empty, faculty)
    with pytest.raises(ValidationFailed):
        svc.create_manual_session(faculty, empty.id, date(2026, 11, 3), [], REASON, now=T0)
    course.is_active = False
    with pytest.raises(ValidationFailed):
        svc.create_manual_session(faculty, course.id, date(2026, 11, 3), [], REASON, now=T0)


# ---- permissions -------------------------------------------------------------

def test_admin_and_co_teacher_can_edit_others_cannot(closed_session):
    faculty, course, students, session = closed_session
    admin, co_teacher, outsider = make_user(role=Role.ADMIN), make_user(role=Role.FACULTY), make_user(role=Role.FACULTY)
    assign_faculty(course, co_teacher)
    svc.set_status(admin, session.id, "stu001", "present", REASON)
    svc.set_status(co_teacher, session.id, "stu001", "absent", "Correction by co-teacher")
    for actor in (outsider, students[0], None):
        with pytest.raises(PermissionDenied):
            svc.set_status(actor, session.id, "stu001", "present", REASON)
        with pytest.raises(PermissionDenied):
            svc.create_manual_session(actor, course.id, date(2026, 11, 3), [], REASON, now=T0)


@pytest.mark.parametrize("bad_id", [0, 99999, None, True])
def test_unknown_session_or_course(klass, bad_id):
    faculty, _, _ = klass
    with pytest.raises(NotFound):
        svc.set_status(faculty, bad_id, "stu001", "present", REASON)
    with pytest.raises(NotFound):
        svc.create_manual_session(faculty, bad_id, date(2026, 11, 3), [], REASON, now=T0)


# ---- the audit trail cannot be rewritten ---------------------------------------

def test_audit_rows_survive_tampering_attempts(closed_session):
    faculty, _, _, session = closed_session
    svc.set_status(faculty, session.id, "stu001", "present", REASON)
    db.session.commit()
    for statement in ("UPDATE attendance_audit SET reason = 'nothing happened'", "DELETE FROM attendance_audit"):
        with pytest.raises(DBAPIError):
            db.session.execute(text(statement))
        db.session.rollback()
    assert AttendanceAudit.query.one().reason == REASON


# ---- reading -----------------------------------------------------------------

def test_roster_of_an_open_session_includes_not_yet_checked_in(klass):
    faculty, course, students = klass
    session, _ = session_service.start_session(faculty, course.id, now=T0)
    make_record(session=session, student=students[0], status="present", method="qr_face")
    _, rows = svc.session_roster(faculty, session.id, now=T0 + timedelta(minutes=1))
    assert [(r.student.username, r.status) for r in rows] == [("stu000", "present"), ("stu001", None), ("stu002", None)]


def test_roster_of_a_closed_session_counts_changes(closed_session):
    faculty, _, _, session = closed_session
    svc.set_status(faculty, session.id, "stu001", "present", REASON)
    svc.set_status(faculty, session.id, "stu001", "absent", "Changed my mind after checking")
    _, rows = svc.session_roster(faculty, session.id)
    assert {r.student.username: r.changes for r in rows} == {"stu000": 0, "stu001": 2, "stu002": 0}


def test_list_sessions_newest_first_with_counts(closed_session):
    faculty, course, _, live = closed_session
    manual, _ = svc.create_manual_session(faculty, course.id, date(2026, 11, 3), ["stu000"], REASON, now=T0)
    _, sessions = svc.list_sessions(faculty, course.id)
    assert [(s.id, present, absent) for s, present, absent in sessions] == [(live.id, 1, 2), (manual.id, 1, 2)]
    with pytest.raises(PermissionDenied):
        svc.list_sessions(make_user(role=Role.FACULTY), course.id)


def test_history_for_student_without_record(klass):
    faculty, course, _ = klass
    session, _ = session_service.start_session(faculty, course.id, now=T0)
    with pytest.raises(NotFound):
        svc.record_history(faculty, session.id, "stu001")
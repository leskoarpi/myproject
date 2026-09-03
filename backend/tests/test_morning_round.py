"""The morning round: room condition and resident status in one pass.

Replaces the old separate morning snapshot. The teacher walks the floor once,
rates each room and sets every resident's status on the same screen.
"""

import datetime as dt

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.accounts.capabilities import Role
from apps.inspections.models import (
    InspectionState,
    RoomCheck,
    RoomCheckHistory,
    RoomCheckStudentResult,
)
from apps.inspections.services import (
    close_room_check_session,
    open_room_check_session,
    reopen_room_check_session,
    room_check_progress,
    save_room_check,
    save_student_morning_status,
)
from apps.presence.models import PresenceEvent, PresenceSource

from . import factories as f

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 9, 3)


@pytest.fixture
def setup():
    f.seed_status_types()
    f.seed_modules()
    year = f.make_school_year()
    room = f.make_room(floor=1, capacity=4)
    students = [f.make_student() for _ in range(2)]
    for student in students:
        f.assign(student, room, school_year=year)
    return {
        "admin": f.make_user(Role.ADMIN),
        "teacher": f.make_teacher(),
        "room": room,
        "students": students,
        "session": None,
    }


@pytest.fixture
def session(setup):
    return open_room_check_session(date=TODAY, floor=1, actor=setup["admin"])


def test_session_prefills_a_row_per_room(setup, session):
    assert session.checks.count() == 1
    assert session.checks.first().room == setup["room"]


def test_rating_and_status_are_recorded_in_one_session(setup, session):
    check = session.checks.first()
    student = setup["students"][0]

    save_room_check(room_check=check, actor=setup["admin"], rating=4, problems="por")
    result = save_student_morning_status(
        room_check=check, student=student, actor=setup["admin"], status="no_school"
    )

    check.refresh_from_db()
    assert check.rating == 4
    assert result.status.code == "no_school"


def test_status_moves_the_students_live_presence(setup, session):
    student = setup["students"][0]
    save_student_morning_status(
        room_check=session.checks.first(),
        student=student,
        actor=setup["admin"],
        status="school",
    )

    student.refresh_from_db()
    assert student.presence.status.code == "school"

    event = PresenceEvent.objects.filter(student=student).first()
    assert event.source == PresenceSource.ROOM_CHECK


def test_result_freezes_name_and_room(setup, session):
    student = setup["students"][0]
    result = save_student_morning_status(
        room_check=session.checks.first(),
        student=student,
        actor=setup["admin"],
        status="inside",
    )
    original_name = result.student_name
    original_room = result.room_number

    student.full_name = "Új Név"
    student.save()
    student.room_assignments.update(is_active=False, end_date=TODAY)
    f.assign(student, f.make_room(floor=2))

    result.refresh_from_db()
    assert result.student_name == original_name != "Új Név"
    assert result.room_number == original_room


def test_a_student_from_another_room_is_rejected(setup, session):
    elsewhere = f.make_student()
    f.assign(elsewhere, f.make_room(floor=1))

    with pytest.raises(ValidationError):
        save_student_morning_status(
            room_check=session.checks.first(),
            student=elsewhere,
            actor=setup["admin"],
            status="inside",
        )


def test_one_result_per_student_per_check(setup, session):
    check = session.checks.first()
    student = setup["students"][0]

    save_student_morning_status(
        room_check=check, student=student, actor=setup["admin"], status="inside"
    )
    save_student_morning_status(
        room_check=check, student=student, actor=setup["admin"], status="doctor"
    )

    results = RoomCheckStudentResult.objects.filter(room_check=check, student=student)
    assert results.count() == 1
    assert results.first().status.code == "doctor"


def test_progress_counts_rooms_and_students(setup, session):
    progress = room_check_progress(session)
    assert progress["rooms_total"] == 1
    assert progress["students_total"] == 2
    assert progress["students_missing"] == 2

    save_student_morning_status(
        room_check=session.checks.first(),
        student=setup["students"][0],
        actor=setup["admin"],
        status="inside",
    )
    assert room_check_progress(session)["students_missing"] == 1


def test_closing_requires_every_student_unless_forced(setup, session):
    with pytest.raises(ValidationError):
        close_room_check_session(session=session, actor=setup["admin"])

    closed = close_room_check_session(
        session=session, actor=setup["admin"], allow_incomplete=True
    )
    assert closed.state == InspectionState.CLOSED


def test_closed_session_refuses_both_kinds_of_edit(setup, session):
    check = session.checks.first()
    close_room_check_session(session=session, actor=setup["admin"], allow_incomplete=True)

    with pytest.raises(ValidationError):
        save_room_check(room_check=check, actor=setup["admin"], rating=3)
    with pytest.raises(ValidationError):
        save_student_morning_status(
            room_check=check,
            student=setup["students"][0],
            actor=setup["admin"],
            status="inside",
        )
    assert RoomCheck.objects.get(pk=check.pk).rating is None


def test_saving_a_rating_archives_the_previous_version(setup, session):
    check = session.checks.first()
    save_room_check(room_check=check, actor=setup["admin"], rating=4, problems="por")
    assert RoomCheckHistory.objects.count() == 0  # nothing to archive on the first save

    save_room_check(room_check=check, actor=setup["admin"], rating=2, problems="rendetlen")

    history = RoomCheckHistory.objects.get()
    assert history.previous_rating == 4
    assert history.previous_problems == "por"


def test_rating_must_be_within_the_scale(setup, session):
    with pytest.raises(ValidationError):
        save_room_check(room_check=session.checks.first(), actor=setup["admin"], rating=9)


def test_reopen_needs_the_capability(setup, session):
    close_room_check_session(session=session, actor=setup["admin"], allow_incomplete=True)

    with pytest.raises(PermissionDenied):
        reopen_room_check_session(session=session, actor=setup["teacher"].user)

    assert (
        reopen_room_check_session(session=session, actor=setup["admin"]).state
        == InspectionState.OPEN
    )


def test_the_morning_round_is_reachable_over_http(client, setup):
    client.force_login(setup["admin"])
    response = client.get(
        reverse("inspections:roomcheck_floor", kwargs={"date": TODAY.isoformat(), "floor": 1})
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert "Reggeli ellenőrzés" in body
    assert "Lakók reggeli státusza" in body


def test_the_old_morning_urls_are_gone():
    from django.urls import NoReverseMatch

    with pytest.raises(NoReverseMatch):
        reverse("inspections:morning_index")

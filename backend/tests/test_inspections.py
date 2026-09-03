"""Evening inspection workflow, locking and state transitions (spec 16-18).

The morning/room round lives in tests/test_morning_round.py."""

import datetime as dt

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.capabilities import Role
from apps.dormcalendar.services import get_or_create_day
from apps.inspections.models import EveningCheckResult, InspectionState
from apps.inspections.services import (
    close_evening_session,
    open_evening_session,
    reopen_evening_session,
    save_evening_result,
)
from apps.inspections.services.evening import SessionLocked, acquire_session_lock
from apps.presence.models import PresenceEvent, PresenceSource

from . import factories as f

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    f.seed_status_types()
    f.seed_modules()
    year = f.make_school_year()
    room = f.make_room(floor=1, capacity=4)
    students = [f.make_student() for _ in range(3)]
    for student in students:
        f.assign(student, room, school_year=year)
    admin = f.make_user(Role.ADMIN)
    teacher = f.make_teacher()
    day = get_or_create_day(dt.date(2025, 11, 11))
    return {
        "admin": admin,
        "teacher": teacher,
        "day": day,
        "room": room,
        "students": students,
    }


def test_session_is_created_once_per_day_and_floor(setup):
    first = open_evening_session(calendar_day=setup["day"], floor=1, actor=setup["admin"])
    second = open_evening_session(calendar_day=setup["day"], floor=1, actor=setup["admin"])
    assert first.pk == second.pk


def test_saving_a_result_also_moves_current_presence(setup):
    session = open_evening_session(calendar_day=setup["day"], floor=1, actor=setup["admin"])
    student = setup["students"][0]

    save_evening_result(
        session=session, student=student, status="outside", actor=setup["admin"]
    )

    student.refresh_from_db()
    assert student.presence.status.code == "outside"
    event = PresenceEvent.objects.filter(student=student).first()
    assert event.source == PresenceSource.EVENING_CHECK


def test_student_from_another_floor_is_rejected(setup):
    session = open_evening_session(calendar_day=setup["day"], floor=1, actor=setup["admin"])
    elsewhere = f.make_student()
    f.assign(elsewhere, f.make_room(floor=2))

    with pytest.raises(ValidationError):
        save_evening_result(
            session=session, student=elsewhere, status="inside", actor=setup["admin"]
        )


def test_second_editor_is_blocked_by_the_lock(setup):
    session = open_evening_session(calendar_day=setup["day"], floor=1, actor=setup["admin"])
    acquire_session_lock(session=session, actor=setup["admin"])

    with pytest.raises(SessionLocked):
        acquire_session_lock(session=session, actor=setup["teacher"].user)


def test_a_stale_lock_can_be_taken_over(setup, settings):
    settings.INSPECTION_LOCK_TIMEOUT_SECONDS = 0
    session = open_evening_session(calendar_day=setup["day"], floor=1, actor=setup["admin"])
    acquire_session_lock(session=session, actor=setup["admin"])

    taken = acquire_session_lock(session=session, actor=setup["teacher"].user)
    assert taken.locked_by_id == setup["teacher"].user.pk


def test_closing_requires_every_student_unless_forced(setup):
    session = open_evening_session(calendar_day=setup["day"], floor=1, actor=setup["admin"])
    save_evening_result(
        session=session, student=setup["students"][0], status="inside", actor=setup["admin"]
    )

    with pytest.raises(ValidationError):
        close_evening_session(session=session, actor=setup["admin"])

    closed = close_evening_session(
        session=session, actor=setup["admin"], allow_incomplete=True
    )
    assert closed.state == InspectionState.CLOSED


def test_closed_session_refuses_edits(setup):
    session = open_evening_session(calendar_day=setup["day"], floor=1, actor=setup["admin"])
    close_evening_session(session=session, actor=setup["admin"], allow_incomplete=True)

    with pytest.raises(ValidationError):
        save_evening_result(
            session=session, student=setup["students"][0], status="inside", actor=setup["admin"]
        )


def test_only_authorized_users_can_reopen(setup):
    session = open_evening_session(calendar_day=setup["day"], floor=1, actor=setup["admin"])
    close_evening_session(session=session, actor=setup["admin"], allow_incomplete=True)

    with pytest.raises(PermissionDenied):
        reopen_evening_session(session=session, actor=setup["teacher"].user)

    reopened = reopen_evening_session(session=session, actor=setup["admin"], reason="javítás")
    assert reopened.state == InspectionState.OPEN
    assert reopened.reopen_reason == "javítás"


def test_one_result_per_student_per_session(setup):
    session = open_evening_session(calendar_day=setup["day"], floor=1, actor=setup["admin"])
    student = setup["students"][0]

    save_evening_result(session=session, student=student, status="inside", actor=setup["admin"])
    save_evening_result(session=session, student=student, status="outside", actor=setup["admin"])

    results = EveningCheckResult.objects.filter(session=session, student=student)
    assert results.count() == 1
    assert results.first().status.code == "outside"


def test_evening_check_is_refused_on_a_day_that_does_not_need_one(setup):
    day = get_or_create_day(dt.date(2025, 11, 15))  # Saturday
    day.evening_check_required = False
    day.save()

    with pytest.raises(ValidationError):
        open_evening_session(calendar_day=day, floor=1, actor=setup["admin"])

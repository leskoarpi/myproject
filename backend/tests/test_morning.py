"""The morning cutoff rule and snapshot generation (spec 19-22, 62).

The cutoff edge cases around midnight and 04:00 are the point of this file.
"""

import datetime as dt

import pytest
from django.utils import timezone

from apps.accounts.capabilities import Role
from apps.dormcalendar.services import get_or_create_day
from apps.inspections.models import MorningResult, MorningSnapshot, MorningSnapshotItem
from apps.inspections.services import (
    calculate_morning_result,
    cutoff_for_date,
    generate_morning_snapshot,
    open_evening_session,
    save_evening_result,
    status_at,
)
from apps.inspections.tasks import generate_morning_snapshot_task
from apps.presence.services import change_student_presence, ensure_presence_row

from . import factories as f

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# The pure rule
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "evening_inside,cutoff_inside,expected",
    [
        (True, True, MorningResult.PRESENT),
        (True, False, MorningResult.LEFT_OVERNIGHT),
        (False, True, MorningResult.RETURNED_LATE),
        (False, False, MorningResult.ABSENT),
        (None, True, MorningResult.PRESENT),
        (None, False, MorningResult.ABSENT),
        (True, None, MorningResult.PRESENT),
        (False, None, MorningResult.ABSENT),
        (None, None, MorningResult.UNKNOWN),
    ],
)
def test_calculate_morning_result_truth_table(evening_inside, cutoff_inside, expected):
    assert (
        calculate_morning_result(evening_inside=evening_inside, cutoff_inside=cutoff_inside)
        == expected
    )


def test_cutoff_is_local_four_am():
    cutoff = cutoff_for_date(dt.date(2025, 11, 12))
    local = timezone.localtime(cutoff)
    assert (local.hour, local.minute) == (4, 0)
    assert local.date() == dt.date(2025, 11, 12)


# --------------------------------------------------------------------------
# status_at: edges around midnight and the cutoff
# --------------------------------------------------------------------------


@pytest.fixture
def student():
    f.seed_status_types()
    f.seed_modules()
    f.make_school_year()
    student = f.make_student()
    ensure_presence_row(student)
    return student


def _move(student, code, when):
    return change_student_presence(
        student=student,
        new_status=code,
        actor=None,
        enforce_permissions=False,
        when=when,
        reason="teszt",
    )


def test_status_at_ignores_events_after_the_moment(student):
    _move(student, "outside", f.aware(2025, 11, 11, 22, 0))
    _move(student, "inside", f.aware(2025, 11, 12, 6, 0))

    cutoff = cutoff_for_date(dt.date(2025, 11, 12))
    assert status_at(student, cutoff).code == "outside"


def test_status_change_exactly_at_cutoff_counts(student):
    """Documented rule: the cutoff is inclusive."""
    _move(student, "outside", f.aware(2025, 11, 11, 23, 0))
    _move(student, "inside", cutoff_for_date(dt.date(2025, 11, 12)))

    assert status_at(student, cutoff_for_date(dt.date(2025, 11, 12))).code == "inside"


def test_change_one_second_after_cutoff_does_not_count(student):
    _move(student, "outside", f.aware(2025, 11, 11, 23, 0))
    _move(student, "inside", cutoff_for_date(dt.date(2025, 11, 12)) + dt.timedelta(seconds=1))

    assert status_at(student, cutoff_for_date(dt.date(2025, 11, 12))).code == "outside"


def test_change_just_before_midnight_is_seen_by_the_next_morning(student):
    _move(student, "night_leave", f.aware(2025, 11, 11, 23, 59))
    assert status_at(student, cutoff_for_date(dt.date(2025, 11, 12))).code == "night_leave"


def test_no_history_before_cutoff_gives_none(student):
    _move(student, "outside", f.aware(2025, 11, 12, 10, 0))
    assert status_at(student, cutoff_for_date(dt.date(2025, 11, 12))) is None


# --------------------------------------------------------------------------
# Snapshot generation
# --------------------------------------------------------------------------


@pytest.fixture
def evening_setup(student):
    admin = f.make_user(Role.ADMIN)
    room = f.make_room(floor=1)
    f.assign(student, room)
    day = get_or_create_day(dt.date(2025, 11, 11))
    session = open_evening_session(calendar_day=day, floor=1, actor=admin)
    return {"admin": admin, "room": room, "session": session, "student": student}


def test_snapshot_freezes_name_and_room(evening_setup):
    student = evening_setup["student"]
    save_evening_result(
        session=evening_setup["session"],
        student=student,
        status="inside",
        actor=evening_setup["admin"],
    )

    snapshot = generate_morning_snapshot(dt.date(2025, 11, 12))
    item = snapshot.items.get(student=student)
    original_name = item.student_name
    original_room = item.room_number

    # Everything about the student changes afterwards...
    student.full_name = "Új Név"
    student.save()
    other_room = f.make_room(floor=2)
    student.room_assignments.update(is_active=False, end_date=dt.date(2025, 11, 20))
    f.assign(student, other_room)

    item.refresh_from_db()
    assert item.student_name == original_name != "Új Név"
    assert item.room_number == original_room


def test_generation_is_idempotent(evening_setup):
    save_evening_result(
        session=evening_setup["session"],
        student=evening_setup["student"],
        status="inside",
        actor=evening_setup["admin"],
    )

    first = generate_morning_snapshot(dt.date(2025, 11, 12))
    second = generate_morning_snapshot(dt.date(2025, 11, 12))

    assert first.pk == second.pk
    assert MorningSnapshot.objects.count() == 1
    assert MorningSnapshotItem.objects.filter(snapshot=first).count() == 1


def test_scheduled_task_is_idempotent(evening_setup):
    generate_morning_snapshot_task(morning_date="2025-11-12")
    generate_morning_snapshot_task(morning_date="2025-11-12")
    assert MorningSnapshot.objects.count() == 1


def test_snapshot_uses_evening_result_and_cutoff_state(evening_setup):
    student = evening_setup["student"]
    save_evening_result(
        session=evening_setup["session"],
        student=student,
        status="inside",
        actor=evening_setup["admin"],
    )
    # Slipped out during the night.
    _move(student, "outside", f.aware(2025, 11, 12, 2, 30))

    snapshot = generate_morning_snapshot(dt.date(2025, 11, 12))
    item = snapshot.items.get(student=student)

    assert item.evening_status_code == "inside"
    assert item.cutoff_status_code == "outside"
    assert item.calculated_result == MorningResult.LEFT_OVERNIGHT
    assert item.final_result == MorningResult.LEFT_OVERNIGHT


def test_review_keeps_the_calculated_value(evening_setup):
    from apps.inspections.services import review_morning_item

    save_evening_result(
        session=evening_setup["session"],
        student=evening_setup["student"],
        status="inside",
        actor=evening_setup["admin"],
    )
    snapshot = generate_morning_snapshot(dt.date(2025, 11, 12))
    item = snapshot.items.first()
    calculated = item.calculated_result

    review_morning_item(
        item=item,
        actor=evening_setup["admin"],
        final_result=MorningResult.ABSENT,
        note="tévedés",
    )

    item.refresh_from_db()
    assert item.calculated_result == calculated
    assert item.final_result == MorningResult.ABSENT
    assert item.is_reviewed is True
    assert item.was_corrected is True

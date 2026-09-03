"""Weekend stays, guests, approvals and checks (spec 29-35)."""

import datetime as dt

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.capabilities import Role
from apps.rooms.models import RoomAssignment
from apps.weekend.models import (
    StayStatus,
    WeekendCheckResult,
    WeekendCheckType,
    WeekendStay,
    friday_of,
)
from apps.weekend.services import (
    close_weekend_check_session,
    create_guest_stay,
    open_weekend_check_session,
    record_weekend_check,
    reopen_weekend_check_session,
    review_weekend_stay,
    submit_weekend_stay,
    weekend_roster,
    weekend_stay_queryset_for_user,
)

from . import factories as f

pytestmark = pytest.mark.django_db

FRIDAY = dt.date(2025, 11, 14)


@pytest.mark.parametrize(
    "day,expected",
    [
        (dt.date(2025, 11, 10), dt.date(2025, 11, 14)),  # Monday -> coming Friday
        (dt.date(2025, 11, 13), dt.date(2025, 11, 14)),  # Thursday -> tomorrow
        (dt.date(2025, 11, 14), dt.date(2025, 11, 14)),  # Friday -> itself
        (dt.date(2025, 11, 15), dt.date(2025, 11, 14)),  # Saturday -> yesterday
        (dt.date(2025, 11, 16), dt.date(2025, 11, 14)),  # Sunday -> this weekend
    ],
)
def test_friday_of_picks_the_right_weekend(day, expected):
    assert friday_of(day) == expected


@pytest.fixture
def setup():
    f.seed_status_types()
    f.seed_modules()
    f.make_school_year()
    group = f.make_group()
    student = f.make_student(group=group)
    f.assign(student, f.make_room(floor=2))
    return {
        "student": student,
        "group": group,
        "management": f.make_user(Role.MANAGEMENT),
        "teacher": f.make_teacher(group=group),
    }


def test_student_submits_and_resubmits(setup):
    student = setup["student"]
    stay = submit_weekend_stay(
        student=student, actor=student.user, weekend_start=FRIDAY, friday_stay=True
    )
    assert stay.status == StayStatus.PENDING
    assert stay.room_number == student.current_room.number

    again = submit_weekend_stay(
        student=student,
        actor=student.user,
        weekend_start=FRIDAY,
        friday_stay=True,
        saturday_stay=True,
    )
    assert again.pk == stay.pk
    assert WeekendStay.objects.count() == 1


def test_at_least_one_night_is_required(setup):
    student = setup["student"]
    with pytest.raises(ValidationError):
        submit_weekend_stay(student=student, actor=student.user, weekend_start=FRIDAY)


def test_student_cannot_submit_for_someone_else(setup):
    other = f.make_student()
    with pytest.raises(PermissionDenied):
        submit_weekend_stay(
            student=other, actor=setup["student"].user, weekend_start=FRIDAY, friday_stay=True
        )


def test_approval_transitions_and_locks_the_student_out(setup):
    student = setup["student"]
    stay = submit_weekend_stay(
        student=student, actor=student.user, weekend_start=FRIDAY, friday_stay=True
    )

    review_weekend_stay(stay=stay, actor=setup["management"], approve=True, note="rendben")
    stay.refresh_from_db()
    assert stay.status == StayStatus.APPROVED
    assert stay.reviewed_by == setup["management"]

    with pytest.raises(ValidationError):
        submit_weekend_stay(
            student=student, actor=student.user, weekend_start=FRIDAY, saturday_stay=True
        )


def test_guest_does_not_consume_a_room_assignment(setup):
    before = RoomAssignment.objects.count()
    room = f.make_room(floor=3)

    guest = create_guest_stay(
        actor=setup["management"],
        weekend_start=FRIDAY,
        guest_name="Vendég Viktor",
        room=room,
        friday_stay=True,
    )

    assert guest.is_guest is True
    assert guest.student_id is None
    assert guest.status == StayStatus.APPROVED
    assert RoomAssignment.objects.count() == before


def test_guest_creation_needs_management(setup):
    with pytest.raises(PermissionDenied):
        create_guest_stay(
            actor=setup["teacher"].user,
            weekend_start=FRIDAY,
            guest_name="Vendég",
            friday_stay=True,
        )


def test_weekend_must_start_on_a_friday(setup):
    with pytest.raises(ValidationError):
        create_guest_stay(
            actor=setup["management"],
            weekend_start=dt.date(2025, 11, 12),
            guest_name="Vendég",
            friday_stay=True,
        )


def test_check_session_only_covers_the_relevant_night(setup):
    student = setup["student"]
    friday_only = submit_weekend_stay(
        student=student, actor=student.user, weekend_start=FRIDAY, friday_stay=True
    )
    review_weekend_stay(stay=friday_only, actor=setup["management"], approve=True)

    saturday_guest = create_guest_stay(
        actor=setup["management"],
        weekend_start=FRIDAY,
        guest_name="Csak szombat",
        saturday_stay=True,
    )

    friday_session = open_weekend_check_session(
        weekend_start=FRIDAY,
        check_type=WeekendCheckType.FRIDAY_EVENING,
        actor=setup["management"],
    )
    saturday_session = open_weekend_check_session(
        weekend_start=FRIDAY,
        check_type=WeekendCheckType.SATURDAY_EVENING,
        actor=setup["management"],
    )

    assert list(friday_session.checks.values_list("stay_id", flat=True)) == [friday_only.pk]
    assert list(saturday_session.checks.values_list("stay_id", flat=True)) == [saturday_guest.pk]


def test_recording_and_closing_a_check(setup):
    student = setup["student"]
    stay = submit_weekend_stay(
        student=student, actor=student.user, weekend_start=FRIDAY, friday_stay=True
    )
    review_weekend_stay(stay=stay, actor=setup["management"], approve=True)
    session = open_weekend_check_session(
        weekend_start=FRIDAY,
        check_type=WeekendCheckType.FRIDAY_EVENING,
        actor=setup["management"],
    )

    check = record_weekend_check(
        session=session,
        stay=stay,
        actor=setup["management"],
        result=WeekendCheckResult.INSIDE,
    )
    assert check.result == WeekendCheckResult.INSIDE

    close_weekend_check_session(session=session, actor=setup["management"])
    with pytest.raises(ValidationError):
        record_weekend_check(
            session=session,
            stay=stay,
            actor=setup["management"],
            result=WeekendCheckResult.OUTSIDE,
        )

    reopen_weekend_check_session(session=session, actor=setup["management"], reason="javítás")
    record_weekend_check(
        session=session, stay=stay, actor=setup["management"], result=WeekendCheckResult.OUTSIDE
    )


def test_roster_carries_every_check_column(setup):
    student = setup["student"]
    stay = submit_weekend_stay(
        student=student,
        actor=student.user,
        weekend_start=FRIDAY,
        friday_stay=True,
        saturday_stay=True,
    )
    review_weekend_stay(stay=stay, actor=setup["management"], approve=True)

    for check_type in WeekendCheckType.values:
        session = open_weekend_check_session(
            weekend_start=FRIDAY, check_type=check_type, actor=setup["management"]
        )
        record_weekend_check(
            session=session,
            stay=stay,
            actor=setup["management"],
            result=WeekendCheckResult.INSIDE,
        )

    rows = weekend_roster(FRIDAY)
    assert len(rows) == 1
    row = rows[0]
    for key in ("friday_evening", "saturday_morning", "saturday_evening", "sunday_morning"):
        assert row[key] is not None
        assert row[key].result == WeekendCheckResult.INSIDE


def test_teacher_scope_limits_visible_stays(setup):
    own = submit_weekend_stay(
        student=setup["student"],
        actor=setup["student"].user,
        weekend_start=FRIDAY,
        friday_stay=True,
    )
    outsider = f.make_student(group=f.make_group())
    other = submit_weekend_stay(
        student=outsider, actor=outsider.user, weekend_start=FRIDAY, friday_stay=True
    )

    visible = weekend_stay_queryset_for_user(setup["teacher"].user, weekend_start=FRIDAY)
    assert own in visible
    assert other not in visible

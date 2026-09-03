"""Regression: a room added to a floor after that day's room-check session
already exists must still get a row - on the session itself, on the floor
page, and on the index page's progress counts. This is exactly the evening
check vs. room check asymmetry: evening never pre-snapshots rooms, so it
never went stale; the room-check session used to.
"""

import datetime as dt

import pytest
from django.urls import reverse

from apps.accounts.capabilities import Role
from apps.inspections.services import open_room_check_session, save_room_check
from apps.inspections.services.roomcheck import sync_room_check_rows
from apps.reports.monthly import room_tidiness_grid
from apps.rooms.services import assign_student_to_room

from . import factories as f

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 9, 10)


@pytest.fixture
def setup():
    f.seed_status_types()
    f.seed_modules()
    year = f.make_school_year()
    admin = f.make_user(Role.ADMIN)
    first_room = f.make_room(number="101", floor=1, capacity=4)
    student = f.make_student()
    f.assign(student, first_room, school_year=year)
    return {"admin": admin, "year": year, "student": student}


def test_a_room_added_after_the_session_opens_gets_no_row_before_the_fix_would_apply(setup):
    """Sets up the exact failure mode, so the assertions below are meaningful."""
    session = open_room_check_session(date=TODAY, floor=1, actor=setup["admin"])
    assert session.checks.count() == 1

    late_room = f.make_room(number="102", floor=1, capacity=2)
    # Without syncing, the new room genuinely has no row yet.
    assert not session.checks.filter(room=late_room).exists()


def test_sync_backfills_the_missing_room(setup):
    session = open_room_check_session(date=TODAY, floor=1, actor=setup["admin"])
    late_room = f.make_room(number="102", floor=1, capacity=2)

    sync_room_check_rows(session)

    assert session.checks.filter(room=late_room).exists()
    assert session.checks.count() == 2


def test_sync_never_touches_an_existing_rating(setup):
    session = open_room_check_session(date=TODAY, floor=1, actor=setup["admin"])
    check = session.checks.first()
    save_room_check(room_check=check, actor=setup["admin"], rating=4, problems="por")

    f.make_room(number="102", floor=1, capacity=2)
    sync_room_check_rows(session)

    check.refresh_from_db()
    assert check.rating == 4
    assert check.problems == "por"


def test_the_floor_page_backfills_on_every_visit(client, setup):
    """The exact reported scenario: session already existed, room came later."""
    open_room_check_session(date=TODAY, floor=1, actor=setup["admin"])
    late_room = f.make_room(number="102", floor=1, capacity=2)
    student2 = f.make_student()
    assign_student_to_room(student=student2, room=late_room, actor=setup["admin"])

    client.force_login(setup["admin"])
    body = client.get(
        reverse(
            "inspections:roomcheck_floor",
            kwargs={"date": TODAY.isoformat(), "floor": 1},
        )
    ).content.decode()

    assert "102" in body
    assert student2.full_name in body


def test_a_new_floor_created_after_visiting_an_empty_session_also_recovers(client, setup):
    """The precise reported case: a floor that did not exist yet (0 rooms on
    it), a session was somehow touched for it, then a room and a student
    landed there. Floor 0 in particular is worth pinning: it is falsy in
    Python, a classic source of "if floor" bugs elsewhere in this app."""
    session = open_room_check_session(date=TODAY, floor=0, actor=setup["admin"])
    assert session.checks.count() == 0

    ground_room = f.make_room(number="001", floor=0, capacity=2)
    student = f.make_student()
    assign_student_to_room(student=student, room=ground_room, actor=setup["admin"])

    client.force_login(setup["admin"])
    body = client.get(
        reverse(
            "inspections:roomcheck_floor",
            kwargs={"date": TODAY.isoformat(), "floor": 0},
        )
    ).content.decode()
    assert "001" in body
    assert student.full_name in body

    save_room_check(
        room_check=session.checks.get(room=ground_room), actor=setup["admin"], rating=5
    )
    grid = room_tidiness_grid(TODAY.year, TODAY.month)
    ground_section = next(s for s in grid.sections if s.label == "0. emelet")
    assert ground_section.rows[0]["labels"] == ["001"]
    assert ground_section.rows[0]["cells"][TODAY.day] == 5


def test_the_index_page_progress_count_also_recovers(client, setup):
    open_room_check_session(date=TODAY, floor=1, actor=setup["admin"])
    f.make_room(number="102", floor=1, capacity=2)

    client.force_login(setup["admin"])
    client.get(reverse("inspections:roomcheck_index"), {"date": TODAY.isoformat()})

    from apps.inspections.models import RoomCheckSession

    session = RoomCheckSession.objects.get(date=TODAY, floor=1)
    assert session.checks.count() == 2


def test_a_read_only_viewer_does_not_trigger_the_backfill(client, setup):
    """VIEW_ROOM_CHECKS without EDIT_ROOM_CHECKS should not write anything -
    a GET should never have that side effect for someone who can only look."""
    session = open_room_check_session(date=TODAY, floor=1, actor=setup["admin"])
    f.make_room(number="102", floor=1, capacity=2)

    porter = f.make_user(Role.PORTER)
    porter.extra_capabilities = ["room_checks.view"]
    porter.save()
    porter.refresh_capabilities()

    client.force_login(porter)
    client.get(
        reverse(
            "inspections:roomcheck_floor",
            kwargs={"date": TODAY.isoformat(), "floor": 1},
        )
    )
    session.refresh_from_db()
    assert session.checks.count() == 1

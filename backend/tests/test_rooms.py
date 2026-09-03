"""Room capacity, assignment overlap and CSV import (spec 8, 9, 44, 62)."""

import io

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from apps.accounts.capabilities import Role
from apps.rooms import csv_io
from apps.rooms.models import Room, RoomAssignment, infer_floor
from apps.rooms.services import assign_student_to_room, end_room_assignment

from . import factories as f

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin():
    f.seed_status_types()
    f.make_school_year()
    return f.make_user(Role.ADMIN)


def test_infer_floor_from_number():
    assert infer_floor("101") == 1
    assert infer_floor("312") == 3
    assert infer_floor("12") == 1
    assert infer_floor("abc") is None


def test_capacity_is_enforced(admin):
    room = f.make_room(capacity=2)
    for _ in range(2):
        assign_student_to_room(student=f.make_student(), room=room, actor=admin)

    with pytest.raises(ValidationError):
        assign_student_to_room(student=f.make_student(), room=room, actor=admin)


def test_capacity_can_be_deliberately_exceeded(admin):
    room = f.make_room(capacity=1)
    assign_student_to_room(student=f.make_student(), room=room, actor=admin)
    assignment = assign_student_to_room(
        student=f.make_student(), room=room, actor=admin, allow_overfill=True
    )
    assert assignment.pk is not None
    assert room.occupancy == 2


def test_moving_rooms_closes_the_previous_assignment(admin):
    student = f.make_student()
    first = f.make_room(capacity=4)
    second = f.make_room(capacity=4)

    assign_student_to_room(student=student, room=first, actor=admin)
    assign_student_to_room(student=student, room=second, actor=admin)

    assert student.current_room == second
    old = RoomAssignment.objects.get(student=student, room=first)
    assert old.is_active is False
    assert old.end_date is not None
    # History survives: both assignments are still on record.
    assert RoomAssignment.objects.filter(student=student).count() == 2


def test_database_refuses_two_active_assignments(admin):
    student = f.make_student()
    f.assign(student, f.make_room())
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            f.assign(student, f.make_room())


def test_ending_an_assignment_frees_the_place(admin):
    room = f.make_room(capacity=1)
    student = f.make_student()
    assignment = assign_student_to_room(student=student, room=room, actor=admin)
    end_room_assignment(assignment=assignment, actor=admin)

    assert room.free_places == 1
    assign_student_to_room(student=f.make_student(), room=room, actor=admin)


def test_teacher_cannot_assign_rooms(admin):
    teacher = f.make_teacher()
    with pytest.raises(PermissionDenied):
        assign_student_to_room(student=f.make_student(), room=f.make_room(), actor=teacher.user)


# --------------------------------------------------------------------------
# CSV import
# --------------------------------------------------------------------------


def _upload(text, name="rooms.csv"):
    return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")


def test_preview_reports_creates_conflicts_and_errors(admin):
    Room.objects.create(number="201", floor=2, capacity=4)
    csv_text = (
        "number,floor,capacity,is_active,notes\n"
        "301,3,4,true,\n"          # new
        "201,2,6,true,\n"          # conflict: capacity differs
        "302,3,notanumber,true,\n" # error
        "301,3,2,true,\n"          # duplicate within the file
    )
    preview = csv_io.build_preview(_upload(csv_text))

    assert [r.data["number"] for r in preview.creates] == ["301"]
    assert [r.data["number"] for r in preview.conflicts] == ["201"]
    assert len(preview.error_rows) == 2
    assert "capacity" in preview.error_rows[0].errors[0]


def test_import_writes_nothing_until_applied(admin):
    csv_io.build_preview(_upload("number,floor,capacity\n401,4,4\n"))
    assert not Room.objects.filter(number="401").exists()


def test_apply_creates_and_respects_overwrite_choice(admin):
    existing = Room.objects.create(number="201", floor=2, capacity=4)
    keep = Room.objects.create(number="202", floor=2, capacity=4)
    csv_text = (
        "number,floor,capacity\n"
        "301,3,4\n"
        "201,2,6\n"
        "202,2,2\n"
    )
    preview = csv_io.build_preview(_upload(csv_text))
    summary = csv_io.apply_import(
        rows=preview.to_session(), actor=admin, overwrite_ids=[existing.pk]
    )

    existing.refresh_from_db()
    keep.refresh_from_db()
    assert summary == {"created": 1, "updated": 1, "skipped": 1}
    assert existing.capacity == 6
    assert keep.capacity == 4  # not selected for overwrite
    assert Room.objects.filter(number="301").exists()


def test_apply_rolls_back_entirely_on_a_late_failure(admin):
    """A capacity below current occupancy aborts the whole import."""
    full_room = Room.objects.create(number="205", floor=2, capacity=4)
    for _ in range(3):
        assign_student_to_room(student=f.make_student(), room=full_room, actor=admin)

    csv_text = "number,floor,capacity\n501,5,4\n205,2,1\n"
    preview = csv_io.build_preview(_upload(csv_text))

    with pytest.raises(ValidationError):
        csv_io.apply_import(
            rows=preview.to_session(), actor=admin, overwrite_ids=[full_room.pk]
        )

    full_room.refresh_from_db()
    assert full_room.capacity == 4
    assert not Room.objects.filter(number="501").exists()


def test_malformed_csv_is_rejected(admin):
    with pytest.raises(ValidationError):
        csv_io.build_preview(_upload("nonsense\n"))
    with pytest.raises(ValidationError):
        csv_io.build_preview(_upload("number,floor,capacity\n"))


def test_import_requires_the_capability(admin):
    porter = f.make_user(Role.PORTER)
    preview = csv_io.build_preview(_upload("number,floor,capacity\n601,6,2\n"))
    with pytest.raises(PermissionDenied):
        csv_io.apply_import(rows=preview.to_session(), actor=porter)


def test_export_round_trips(admin):
    Room.objects.create(number="701", floor=7, capacity=2, notes="teszt")
    payload = csv_io.export_csv()
    assert "701" in payload
    assert payload.splitlines()[0] == ",".join(csv_io.COLUMNS)


def test_floors_returns_each_floor_once(admin):
    """Regression: Room.Meta.ordering used to leak `number` into the DISTINCT."""
    from apps.rooms.services import floors

    for floor in (1, 1, 1, 2, 3, 3):
        f.make_room(floor=floor)

    assert floors() == [1, 2, 3]


def test_floors_ignores_inactive_rooms(admin):
    from apps.rooms.services import floors

    f.make_room(floor=1)
    f.make_room(floor=9, is_active=False)

    assert floors() == [1]

"""Room and room-assignment services."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.capabilities import Capability
from apps.audit.services import AuditAction, model_snapshot, record_audit
from apps.core.models import SchoolYear

from .models import Room, RoomAssignment

ASSIGNMENT_AUDIT_FIELDS = ("student", "room", "school_year", "start_date", "end_date", "is_active")


@transaction.atomic
def assign_student_to_room(
    *, student, room, actor, school_year=None, start_date=None, allow_overfill=False, note=""
):
    """Move ``student`` into ``room``.

    Ends any current assignment, then creates the new one. Capacity is checked
    against a row-locked room so two concurrent moves cannot both squeeze in.
    """
    if not actor.has_capability(Capability.MANAGE_ASSIGNMENTS):
        raise PermissionDenied("Missing capability to manage room assignments.")

    school_year = school_year or SchoolYear.current()
    if school_year is None:
        raise ValidationError("No active school year is configured.")

    start_date = start_date or timezone.localdate()
    if not school_year.contains(start_date):
        raise ValidationError("Start date falls outside the selected school year.")

    # Lock the room row for the duration so occupancy cannot race.
    room = Room.objects.select_for_update().get(pk=room.pk)
    if not room.is_active:
        raise ValidationError("Cannot assign a student to an inactive room.")

    current = (
        RoomAssignment.objects.select_for_update()
        .filter(student=student, is_active=True)
        .first()
    )
    if current and current.room_id == room.pk:
        return current
    if current:
        end_room_assignment(assignment=current, actor=actor, end_date=start_date, audit=False)

    occupancy = RoomAssignment.objects.filter(room=room, is_active=True).count()
    if occupancy >= room.capacity and not allow_overfill:
        raise ValidationError(
            f"Room {room.number} is full ({occupancy}/{room.capacity})."
        )

    assignment = RoomAssignment.objects.create(
        student=student,
        room=room,
        school_year=school_year,
        start_date=start_date,
        is_active=True,
        note=note,
        created_by=actor,
    )
    record_audit(
        user=actor,
        action=AuditAction.CREATE,
        target=assignment,
        new_value=model_snapshot(assignment, ASSIGNMENT_AUDIT_FIELDS),
        note="over capacity" if occupancy >= room.capacity else "",
    )
    return assignment


@transaction.atomic
def end_room_assignment(*, assignment, actor, end_date=None, audit=True):
    """Close an assignment without destroying the historical record."""
    if not actor.has_capability(Capability.MANAGE_ASSIGNMENTS):
        raise PermissionDenied("Missing capability to manage room assignments.")

    assignment = RoomAssignment.objects.select_for_update().get(pk=assignment.pk)
    if not assignment.is_active:
        return assignment

    before = model_snapshot(assignment, ASSIGNMENT_AUDIT_FIELDS)
    end_date = end_date or timezone.localdate()
    if end_date < assignment.start_date:
        end_date = assignment.start_date

    assignment.is_active = False
    assignment.end_date = end_date
    assignment.ended_by = actor
    assignment.save(update_fields=["is_active", "end_date", "ended_by", "updated_at"])

    if audit:
        record_audit(
            user=actor,
            action=AuditAction.UPDATE,
            target=assignment,
            old_value=before,
            new_value=model_snapshot(assignment, ASSIGNMENT_AUDIT_FIELDS),
            note="assignment ended",
        )
    return assignment


@transaction.atomic
def save_room(*, actor, room=None, **fields):
    if not actor.has_capability(Capability.MANAGE_ROOMS):
        raise PermissionDenied("Missing capability to manage rooms.")

    audit_fields = ("number", "floor", "capacity", "is_active")
    if room is None:
        room = Room(**fields)
        room.full_clean()
        room.save()
        record_audit(
            user=actor,
            action=AuditAction.CREATE,
            target=room,
            new_value=model_snapshot(room, audit_fields),
        )
        return room

    room = Room.objects.select_for_update().get(pk=room.pk)
    before = model_snapshot(room, audit_fields)
    for key, value in fields.items():
        setattr(room, key, value)

    if room.capacity < room.occupancy:
        raise ValidationError(
            f"Capacity {room.capacity} is below the current occupancy ({room.occupancy})."
        )
    room.full_clean()
    room.save()
    record_audit(
        user=actor,
        action=AuditAction.UPDATE,
        target=room,
        old_value=before,
        new_value=model_snapshot(room, audit_fields),
    )
    return room


def students_on_floor(floor, *, active_only=True):
    """Students currently living on ``floor``, ordered for inspection entry."""
    from apps.students.models import StudentProfile

    qs = StudentProfile.objects.filter(
        room_assignments__is_active=True, room_assignments__room__floor=floor
    )
    if active_only:
        qs = qs.filter(is_active=True)
    return (
        qs.select_related("group")
        .prefetch_related("room_assignments__room")
        .order_by("room_assignments__room__number", "full_name")
        .distinct()
    )


def floors():
    """Distinct floors that have at least one active room.

    The explicit order_by matters: Room.Meta.ordering includes ``number``,
    which would otherwise join the DISTINCT and return one row per room.
    """
    return list(
        Room.objects.active()
        .order_by("floor")
        .values_list("floor", flat=True)
        .distinct()
    )

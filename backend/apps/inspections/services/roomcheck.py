"""Room / cleanliness inspection workflow (spec sections 25-28)."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.capabilities import Capability
from apps.audit.services import AuditAction, record_audit
from apps.core.models import ModuleKey, SystemModule
from apps.rooms.models import Room

from ..models import (
    InspectionState,
    RoomCheck,
    RoomCheckHistory,
    RoomCheckSession,
    RoomCheckStudentResult,
)


def _require_module():
    if not SystemModule.is_module_enabled(ModuleKey.ROOM_CHECKS):
        raise PermissionDenied("The room check module is disabled.")


@transaction.atomic
def open_room_check_session(*, date, floor, actor):
    _require_module()
    if not actor.has_capability(Capability.EDIT_ROOM_CHECKS):
        raise PermissionDenied("Missing capability to run room checks.")

    session, created = RoomCheckSession.objects.get_or_create(
        date=date,
        floor=floor,
        defaults={"opened_by": actor, "opened_at": timezone.now()},
    )
    if created:
        # Pre-create an empty check row per room so the entry sheet is stable.
        RoomCheck.objects.bulk_create(
            [
                RoomCheck(session=session, room=room)
                for room in Room.objects.active().on_floor(floor)
            ],
            ignore_conflicts=True,
        )
        record_audit(user=actor, action=AuditAction.CREATE, target=session)
    return session


@transaction.atomic
def save_room_check(*, room_check, actor, rating=None, problems=None, notes=None):
    """Update a room's condition, archiving the previous values first."""
    _require_module()
    if not actor.has_capability(Capability.EDIT_ROOM_CHECKS):
        raise PermissionDenied("Missing capability to edit room checks.")

    room_check = (
        RoomCheck.objects.select_for_update().select_related("session").get(pk=room_check.pk)
    )
    if room_check.session.state == InspectionState.CLOSED:
        raise ValidationError("This room check session is closed.")

    if rating is not None:
        rating = int(rating)
        if not (RoomCheck.RATING_MIN <= rating <= RoomCheck.RATING_MAX):
            raise ValidationError(
                f"Rating must be between {RoomCheck.RATING_MIN} and {RoomCheck.RATING_MAX}."
            )

    # Versioning: keep what was there before instead of overwriting silently.
    if room_check.is_recorded:
        RoomCheckHistory.objects.create(
            room_check=room_check,
            previous_rating=room_check.rating,
            previous_problems=room_check.problems,
            previous_notes=room_check.notes,
            saved_by=actor,
        )

    before = {
        "rating": room_check.rating,
        "problems": room_check.problems,
        "notes": room_check.notes,
    }
    if rating is not None:
        room_check.rating = rating
    if problems is not None:
        room_check.problems = problems
    if notes is not None:
        room_check.notes = notes
    room_check.checked_by = actor
    room_check.checked_at = timezone.now()
    room_check.full_clean(exclude=["session", "room"])
    room_check.save()

    record_audit(
        user=actor,
        action=AuditAction.UPDATE,
        target=room_check,
        old_value=before,
        new_value={
            "rating": room_check.rating,
            "problems": room_check.problems,
            "notes": room_check.notes,
        },
    )

    session = room_check.session
    session.last_activity_at = timezone.now()
    session.save(update_fields=["last_activity_at", "updated_at"])
    return room_check


@transaction.atomic
def save_room_check_student_result(
    *,
    room_check,
    student,
    actor,
    presence_result,
    detail_code="",
    departure_time=None,
    detail_note="",
):
    _require_module()
    if not actor.has_capability(Capability.EDIT_ROOM_CHECKS):
        raise PermissionDenied("Missing capability to edit room checks.")
    if room_check.session.state == InspectionState.CLOSED:
        raise ValidationError("This room check session is closed.")

    result, _ = RoomCheckStudentResult.objects.update_or_create(
        room_check=room_check,
        student=student,
        defaults={
            "presence_result": presence_result,
            "detail_code": detail_code[:32],
            "departure_time": departure_time,
            "detail_note": detail_note[:255],
            "saved_by": actor,
            "saved_at": timezone.now(),
        },
    )
    return result


@transaction.atomic
def close_room_check_session(*, session, actor):
    _require_module()
    if not actor.has_capability(Capability.EDIT_ROOM_CHECKS):
        raise PermissionDenied("Missing capability to close room checks.")

    session = RoomCheckSession.objects.select_for_update().get(pk=session.pk)
    if session.state == InspectionState.CLOSED:
        return session

    session.state = InspectionState.CLOSED
    session.closed_by = actor
    session.closed_at = timezone.now()
    session.locked_by = None
    session.locked_at = None
    session.save(
        update_fields=["state", "closed_by", "closed_at", "locked_by", "locked_at", "updated_at"]
    )
    record_audit(user=actor, action=AuditAction.CLOSE, target=session)
    return session


@transaction.atomic
def reopen_room_check_session(*, session, actor, reason=""):
    _require_module()
    if not actor.has_capability(Capability.REOPEN_ROOM_CHECKS):
        raise PermissionDenied("Missing capability to reopen room checks.")

    session = RoomCheckSession.objects.select_for_update().get(pk=session.pk)
    if session.state == InspectionState.OPEN:
        return session

    session.state = InspectionState.OPEN
    session.reopened_by = actor
    session.reopened_at = timezone.now()
    session.reopen_reason = reason[:255]
    session.save(
        update_fields=["state", "reopened_by", "reopened_at", "reopen_reason", "updated_at"]
    )
    record_audit(user=actor, action=AuditAction.REOPEN, target=session, note=reason)
    return session


def room_check_queryset_for_user(user):
    """Students see only their own room's checks (spec section 57)."""
    from apps.accounts.capabilities import Capability as Cap

    base = RoomCheck.objects.select_related("room", "session", "checked_by")
    if not user.is_authenticated:
        return base.none()
    if user.has_capability(Cap.VIEW_ROOM_CHECKS) or user.has_capability(
        Cap.VIEW_ROOM_CHECK_HISTORY
    ):
        return base
    if user.has_capability(Cap.VIEW_OWN_ROOM_CHECKS):
        profile = getattr(user, "student_profile", None)
        room = profile.current_room if profile else None
        return base.filter(room=room) if room else base.none()
    return base.none()

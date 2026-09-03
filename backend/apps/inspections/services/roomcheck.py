"""The morning round: room condition and each resident's status, in one pass.

This replaces the former separate morning inspection. A teacher opens the
floor once, rates each room and sets every resident's morning status on the
same screen; the status also moves the student's live presence, sourced as
``room_check`` (spec sections 25-28, reworked).
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.capabilities import Capability
from apps.audit.services import AuditAction, record_audit
from apps.core.models import ModuleKey, SystemModule
from apps.presence.models import PresenceSource, StatusType
from apps.presence.services import change_student_presence
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
        raise PermissionDenied("A reggeli és szobaellenőrzés modul ki van kapcsolva.")


@transaction.atomic
def open_room_check_session(*, date, floor, actor):
    _require_module()
    if not actor.has_capability(Capability.EDIT_ROOM_CHECKS):
        raise PermissionDenied("Nincs jogosultságod reggeli ellenőrzést végezni.")

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
        raise PermissionDenied("Nincs jogosultságod a szobaellenőrzés szerkesztéséhez.")

    room_check = (
        RoomCheck.objects.select_for_update().select_related("session").get(pk=room_check.pk)
    )
    if room_check.session.state == InspectionState.CLOSED:
        raise ValidationError("Ez az ellenőrzés le van zárva.")

    if rating is not None:
        rating = int(rating)
        if not (RoomCheck.RATING_MIN <= rating <= RoomCheck.RATING_MAX):
            raise ValidationError(
                f"Az értékelés {RoomCheck.RATING_MIN} és {RoomCheck.RATING_MAX} között lehet."
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
def save_student_morning_status(
    *, room_check, student, actor, status, note="", sync_presence=True
):
    """Record one resident's morning status during the room round.

    Freezes the student's name, room and class onto the result so the sheet
    stays truthful after a later move or rename, then moves the student's live
    presence to the same status.
    """
    _require_module()
    if not actor.has_capability(Capability.EDIT_ROOM_CHECKS):
        raise PermissionDenied("Nincs jogosultságod a reggeli ellenőrzés szerkesztéséhez.")

    room_check = (
        RoomCheck.objects.select_for_update()
        .select_related("session", "room")
        .get(pk=room_check.pk)
    )
    if room_check.session.state == InspectionState.CLOSED:
        raise ValidationError("Ez az ellenőrzés le van zárva; szerkesztés előtt nyisd újra.")

    if isinstance(status, str):
        resolved = StatusType.objects.filter(code=status, is_active=True).first()
        if resolved is None:
            raise ValidationError(f"Ismeretlen vagy inaktív státusz: „{status}”.")
        status = resolved

    room = student.current_room
    if room is None or room.pk != room_check.room_id:
        raise ValidationError(
            f"{student.full_name} nem a(z) {room_check.room.number} szobában lakik."
        )

    result, _ = RoomCheckStudentResult.objects.update_or_create(
        room_check=room_check,
        student=student,
        defaults={
            "status": status,
            "student_name": student.full_name,
            "room_number": room_check.room.number,
            "school_class": student.school_class,
            "note": note[:255],
            "saved_by": actor,
            "saved_at": timezone.now(),
        },
    )

    if sync_presence:
        change_student_presence(
            student=student,
            new_status=status,
            actor=actor,
            reason=note or "Reggeli ellenőrzés",
            source=PresenceSource.ROOM_CHECK,
            enforce_permissions=False,
        )

    session = room_check.session
    session.last_activity_at = timezone.now()
    session.save(update_fields=["last_activity_at", "updated_at"])
    return result


def room_check_progress(session):
    """How far along one floor's morning round is."""
    from apps.rooms.services import students_on_floor

    rooms_total = session.checks.count()
    rooms_done = session.checks.filter(checked_at__isnull=False).count()
    students_total = students_on_floor(session.floor).count()
    students_done = RoomCheckStudentResult.objects.filter(
        room_check__session=session, status__isnull=False
    ).count()

    total = rooms_total + students_total
    done = rooms_done + students_done
    return {
        "rooms_total": rooms_total,
        "rooms_done": rooms_done,
        "students_total": students_total,
        "students_done": students_done,
        "students_missing": max(students_total - students_done, 0),
        "complete": total > 0 and done >= total,
        "percent": int(round(100 * done / total)) if total else 0,
    }


@transaction.atomic
def close_room_check_session(*, session, actor, allow_incomplete=False):
    _require_module()
    if not actor.has_capability(Capability.EDIT_ROOM_CHECKS):
        raise PermissionDenied("Nincs jogosultságod a reggeli ellenőrzés lezárásához.")

    session = RoomCheckSession.objects.select_for_update().get(pk=session.pk)
    if session.state == InspectionState.CLOSED:
        return session

    progress = room_check_progress(session)
    if progress["students_missing"] and not allow_incomplete:
        raise ValidationError(
            f"{progress['students_missing']} diáknak még nincs reggeli státusza ezen az emeleten."
        )

    session.state = InspectionState.CLOSED
    session.closed_by = actor
    session.closed_at = timezone.now()
    session.locked_by = None
    session.locked_at = None
    session.save(
        update_fields=["state", "closed_by", "closed_at", "locked_by", "locked_at", "updated_at"]
    )
    record_audit(user=actor, action=AuditAction.CLOSE, target=session, new_value=progress)
    return session


@transaction.atomic
def reopen_room_check_session(*, session, actor, reason=""):
    _require_module()
    if not actor.has_capability(Capability.REOPEN_ROOM_CHECKS):
        raise PermissionDenied("Nincs jogosultságod a reggeli ellenőrzés újranyitásához.")

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
    base = RoomCheck.objects.select_related("room", "session", "checked_by")
    if not user.is_authenticated:
        return base.none()
    if user.has_capability(Capability.VIEW_ROOM_CHECKS) or user.has_capability(
        Capability.VIEW_ROOM_CHECK_HISTORY
    ):
        return base
    if user.has_capability(Capability.VIEW_OWN_ROOM_CHECKS):
        profile = getattr(user, "student_profile", None)
        room = profile.current_room if profile else None
        return base.filter(room=room) if room else base.none()
    return base.none()

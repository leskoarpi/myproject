"""Weekend stay and weekend check services (spec sections 29-35)."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.capabilities import Capability
from apps.audit.services import AuditAction, record_audit
from apps.core.models import ModuleKey, SystemModule

from .models import (
    CHECK_REQUIRES_NIGHT,
    InspectionState,
    StayStatus,
    WeekendCheck,
    WeekendCheckResult,
    WeekendCheckSession,
    WeekendCheckType,
    WeekendStay,
    friday_of,
)


def _require_module():
    if not SystemModule.is_module_enabled(ModuleKey.WEEKEND_STAY):
        raise PermissionDenied("The weekend stay module is disabled.")


def weekend_stay_queryset_for_user(user, *, weekend_start=None):
    """Object-level scope for weekend stays."""
    base = WeekendStay.objects.select_related("student", "room", "reviewed_by")
    if weekend_start:
        base = base.filter(weekend_start=weekend_start)

    if not user.is_authenticated:
        return base.none()
    if user.has_capability(Capability.MANAGE_WEEKEND_STAY):
        return base

    teacher = getattr(user, "teacher_profile", None)
    if user.has_capability(Capability.VIEW_WEEKEND_STAY):
        if teacher and teacher.can_manage_all_weekend_stays:
            return base
        if teacher and not teacher.has_all_student_access:
            # Guests have no group, so they stay with management-level users.
            return base.filter(student__group_id__in=teacher.accessible_group_ids())
        return base

    if user.has_capability(Capability.REQUEST_WEEKEND_STAY):
        profile = getattr(user, "student_profile", None)
        return base.filter(student=profile) if profile else base.none()

    return base.none()


@transaction.atomic
def submit_weekend_stay(
    *, student, actor, weekend_start=None, friday_stay=False, saturday_stay=False, note=""
):
    """A student registers for the coming weekend."""
    _require_module()
    if not actor.has_capability(Capability.REQUEST_WEEKEND_STAY) and not actor.has_capability(
        Capability.MANAGE_WEEKEND_STAY
    ):
        raise PermissionDenied("Missing capability to request a weekend stay.")

    own_profile = getattr(actor, "student_profile", None)
    if own_profile is not None and own_profile.pk != student.pk:
        raise PermissionDenied("You may only submit a request for yourself.")
    if own_profile is None and not actor.has_capability(Capability.MANAGE_WEEKEND_STAY):
        raise PermissionDenied("Missing capability to submit on behalf of a student.")

    if not friday_stay and not saturday_stay:
        raise ValidationError("Select at least one night.")

    weekend_start = weekend_start or friday_of(timezone.localdate())
    room = student.current_room

    stay = (
        WeekendStay.objects.select_for_update()
        .filter(student=student, weekend_start=weekend_start)
        .first()
    )
    if stay is not None:
        if stay.status == StayStatus.APPROVED and own_profile is not None:
            raise ValidationError(
                "Your request for this weekend has been approved and can no longer be changed."
            )
        stay.friday_stay = friday_stay
        stay.saturday_stay = saturday_stay
        stay.note = note
        stay.status = StayStatus.PENDING
        stay.reviewed_by = None
        stay.reviewed_at = None
        stay.review_note = ""
        stay.room = room
        stay.room_number = room.number if room else ""
        stay.full_clean()
        stay.save()
        action = AuditAction.UPDATE
    else:
        stay = WeekendStay(
            weekend_start=weekend_start,
            student=student,
            room=room,
            room_number=room.number if room else "",
            group_name=student.group.name if student.group else "",
            display_name=student.full_name,
            friday_stay=friday_stay,
            saturday_stay=saturday_stay,
            note=note,
            submitted_by=actor,
        )
        stay.full_clean()
        stay.save()
        action = AuditAction.CREATE

    record_audit(
        user=actor,
        action=action,
        target=stay,
        new_value={
            "weekend": str(weekend_start),
            "friday": friday_stay,
            "saturday": saturday_stay,
        },
    )
    return stay


@transaction.atomic
def create_guest_stay(
    *,
    actor,
    weekend_start,
    guest_name,
    room=None,
    friday_stay=False,
    saturday_stay=False,
    **guest_fields,
):
    """Management adds a guest for the weekend.

    Deliberately does *not* create a RoomAssignment: a guest does not occupy a
    weekday place (spec section 31).
    """
    _require_module()
    if not actor.has_capability(Capability.MANAGE_WEEKEND_STAY):
        raise PermissionDenied("Missing capability to manage weekend stays.")

    stay = WeekendStay(
        weekend_start=weekend_start,
        is_guest=True,
        guest_name=guest_name,
        display_name=guest_name,
        room=room,
        room_number=room.number if room else "",
        friday_stay=friday_stay,
        saturday_stay=saturday_stay,
        submitted_by=actor,
        status=StayStatus.APPROVED,
        reviewed_by=actor,
        reviewed_at=timezone.now(),
        **{k: v for k, v in guest_fields.items() if v is not None},
    )
    stay.full_clean()
    stay.save()
    record_audit(user=actor, action=AuditAction.CREATE, target=stay, note="guest weekend stay")
    return stay


@transaction.atomic
def review_weekend_stay(*, stay, actor, approve, note=""):
    """Approve or reject a pending request. Students cannot review their own."""
    _require_module()
    if not actor.has_capability(Capability.REVIEW_WEEKEND_STAY):
        raise PermissionDenied("Missing capability to review weekend stays.")

    own_profile = getattr(actor, "student_profile", None)
    if own_profile is not None and stay.student_id == own_profile.pk:
        raise PermissionDenied("You may not review your own request.")

    if not weekend_stay_queryset_for_user(actor).filter(pk=stay.pk).exists():
        raise PermissionDenied("This request is outside your scope.")

    stay = WeekendStay.objects.select_for_update().get(pk=stay.pk)
    if stay.status not in {StayStatus.PENDING, StayStatus.APPROVED, StayStatus.REJECTED}:
        raise ValidationError("This request can no longer be reviewed.")

    previous = stay.status
    stay.status = StayStatus.APPROVED if approve else StayStatus.REJECTED
    stay.reviewed_by = actor
    stay.reviewed_at = timezone.now()
    stay.review_note = note
    stay.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"])

    record_audit(
        user=actor,
        action=AuditAction.APPROVE if approve else AuditAction.REJECT,
        target=stay,
        old_value={"status": previous},
        new_value={"status": stay.status},
        note=note,
    )
    return stay


@transaction.atomic
def cancel_weekend_stay(*, stay, actor):
    _require_module()
    own_profile = getattr(actor, "student_profile", None)
    is_owner = own_profile is not None and stay.student_id == own_profile.pk
    if not is_owner and not actor.has_capability(Capability.MANAGE_WEEKEND_STAY):
        raise PermissionDenied("You may not cancel this request.")
    if is_owner and stay.status == StayStatus.APPROVED:
        raise ValidationError("Approved requests can only be cancelled by staff.")

    stay = WeekendStay.objects.select_for_update().get(pk=stay.pk)
    stay.status = StayStatus.CANCELLED
    stay.save(update_fields=["status", "updated_at"])
    record_audit(user=actor, action=AuditAction.UPDATE, target=stay, note="cancelled")
    return stay


@transaction.atomic
def open_weekend_check_session(*, weekend_start, check_type, actor):
    """Open a check session and materialise a row per relevant approved stay."""
    _require_module()
    if not actor.has_capability(Capability.RUN_WEEKEND_CHECK):
        raise PermissionDenied("Missing capability to run weekend checks.")
    if check_type not in WeekendCheckType.values:
        raise ValidationError(f"Unknown check type '{check_type}'.")

    session, created = WeekendCheckSession.objects.get_or_create(
        weekend_start=weekend_start,
        check_type=check_type,
        defaults={"opened_by": actor, "opened_at": timezone.now()},
    )

    night_field = CHECK_REQUIRES_NIGHT[check_type]
    relevant = WeekendStay.objects.approved().for_weekend(weekend_start).filter(
        **{night_field: True}
    )
    WeekendCheck.objects.bulk_create(
        [WeekendCheck(session=session, stay=stay) for stay in relevant],
        ignore_conflicts=True,
    )
    if created:
        record_audit(user=actor, action=AuditAction.CREATE, target=session)
    return session


@transaction.atomic
def record_weekend_check(*, session, stay, actor, result, note=""):
    _require_module()
    if not actor.has_capability(Capability.RUN_WEEKEND_CHECK):
        raise PermissionDenied("Missing capability to run weekend checks.")
    if result not in WeekendCheckResult.values:
        raise ValidationError(f"Unknown result '{result}'.")

    session = WeekendCheckSession.objects.select_for_update().get(pk=session.pk)
    if session.state == InspectionState.CLOSED:
        raise ValidationError("This check session is closed.")

    check, _ = WeekendCheck.objects.update_or_create(
        session=session,
        stay=stay,
        defaults={
            "result": result,
            "note": note[:255],
            "checked_by": actor,
            "checked_at": timezone.now(),
        },
    )
    session.last_activity_at = timezone.now()
    session.save(update_fields=["last_activity_at", "updated_at"])
    return check


@transaction.atomic
def close_weekend_check_session(*, session, actor):
    _require_module()
    if not actor.has_capability(Capability.RUN_WEEKEND_CHECK):
        raise PermissionDenied("Missing capability to close weekend checks.")

    session = WeekendCheckSession.objects.select_for_update().get(pk=session.pk)
    if session.state == InspectionState.CLOSED:
        return session
    session.state = InspectionState.CLOSED
    session.closed_by = actor
    session.closed_at = timezone.now()
    session.save(update_fields=["state", "closed_by", "closed_at", "updated_at"])
    record_audit(user=actor, action=AuditAction.CLOSE, target=session)
    return session


@transaction.atomic
def reopen_weekend_check_session(*, session, actor, reason=""):
    _require_module()
    if not actor.has_capability(Capability.REOPEN_WEEKEND_CHECK):
        raise PermissionDenied("Missing capability to reopen weekend checks.")

    session = WeekendCheckSession.objects.select_for_update().get(pk=session.pk)
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


def weekend_roster(weekend_start):
    """Rows for the printable weekend report (spec section 35)."""
    stays = list(
        WeekendStay.objects.approved()
        .for_weekend(weekend_start)
        .select_related("student", "room")
        .order_by("room_number", "display_name")
    )
    checks = {
        (c.stay_id, c.session.check_type): c
        for c in WeekendCheck.objects.filter(
            session__weekend_start=weekend_start
        ).select_related("session")
    }

    rows = []
    for stay in stays:
        rows.append(
            {
                "stay": stay,
                "room": stay.room_number,
                "name": stay.display_name,
                "group": stay.group_name,
                "friday_stay": stay.friday_stay,
                "saturday_stay": stay.saturday_stay,
                "friday_evening": checks.get((stay.pk, WeekendCheckType.FRIDAY_EVENING)),
                "saturday_morning": checks.get((stay.pk, WeekendCheckType.SATURDAY_MORNING)),
                "saturday_evening": checks.get((stay.pk, WeekendCheckType.SATURDAY_EVENING)),
                "sunday_morning": checks.get((stay.pk, WeekendCheckType.SUNDAY_MORNING)),
                "note": stay.note,
            }
        )
    return rows

"""Morning inspection: snapshot generation, the cutoff rule, and review.

The cutoff rule (spec section 21) is the single most important business rule
in this module, so it lives in one small pure function,
:func:`calculate_morning_result`, rather than inside a view.

**Definition used here.** A morning snapshot for morning date *M* is built
from the evening inspection of the previous day *M-1* and from the student's
presence state at the cutoff instant (local 04:00 on *M* by default):

===================  ==================  ======================================
evening result       state at cutoff     morning result
===================  ==================  ======================================
inside               inside              PRESENT      - slept in
inside               outside             LEFT_OVERNIGHT - left during the night
outside              inside              RETURNED_LATE  - came back after check
outside              outside             ABSENT
not checked          inside/outside      PRESENT / ABSENT (cutoff state alone)
inside/outside       no history at all   falls back to the evening result
not checked          no history          UNKNOWN
===================  ==================  ======================================

**Documented ambiguity** (spec section 76.24): the legacy system did not make
explicit what should happen when a student's status changes *exactly* at
04:00. Here the cutoff is inclusive - a change stamped 04:00:00 is considered
to have happened *before* the cutoff and therefore counts.
"""

import datetime as dt
import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.capabilities import Capability
from apps.audit.services import AuditAction, record_audit
from apps.core.models import ModuleKey, SystemModule
from apps.dormcalendar.services import get_or_create_day
from apps.presence.models import PresenceEvent, StudentPresence
from apps.students.models import StudentProfile

from ..models import (
    EveningCheckResult,
    InspectionState,
    MorningMonth,
    MorningResult,
    MorningSnapshot,
    MorningSnapshotItem,
)

logger = logging.getLogger(__name__)


def _require_module():
    if not SystemModule.is_module_enabled(ModuleKey.MORNING_CHECK):
        raise PermissionDenied("The morning check module is disabled.")


def cutoff_for_date(morning_date):
    """The evaluation instant for a morning, as an aware datetime."""
    naive = dt.datetime.combine(
        morning_date,
        dt.time(hour=settings.MORNING_CUTOFF_HOUR, minute=settings.MORNING_CUTOFF_MINUTE),
    )
    return timezone.make_aware(naive, timezone.get_current_timezone())


def status_at(student, moment):
    """The student's presence status as it stood at ``moment``.

    Derived from the immutable event log, so it stays correct no matter what
    the student's status is today. Returns ``None`` when no event precedes the
    moment.
    """
    event = (
        PresenceEvent.objects.filter(student=student, created_at__lte=moment)
        .select_related("new_status")
        .order_by("-created_at", "-id")
        .first()
    )
    return event.new_status if event else None


def calculate_morning_result(*, evening_inside, cutoff_inside):
    """Pure decision function. Both arguments are ``True``/``False``/``None``.

    ``None`` means "not known": no evening result recorded, or no presence
    history before the cutoff.
    """
    if evening_inside is None and cutoff_inside is None:
        return MorningResult.UNKNOWN
    if evening_inside is None:
        return MorningResult.PRESENT if cutoff_inside else MorningResult.ABSENT
    if cutoff_inside is None:
        # Nothing happened after the evening check; carry it forward.
        return MorningResult.PRESENT if evening_inside else MorningResult.ABSENT
    if evening_inside and cutoff_inside:
        return MorningResult.PRESENT
    if evening_inside and not cutoff_inside:
        return MorningResult.LEFT_OVERNIGHT
    if not evening_inside and cutoff_inside:
        return MorningResult.RETURNED_LATE
    return MorningResult.ABSENT


@transaction.atomic
def generate_morning_snapshot(morning_date=None, *, actor=None, force=False):
    """Create the snapshot for ``morning_date``. Idempotent.

    Running it twice never creates a second snapshot; the second run returns
    the existing one untouched unless ``force`` is set and the snapshot is
    still open (spec section 22).
    """
    morning_date = morning_date or timezone.localdate()
    evening_date = morning_date - dt.timedelta(days=1)

    calendar_day = get_or_create_day(morning_date)
    month = MorningMonth.for_date(morning_date)
    cutoff = cutoff_for_date(morning_date)

    snapshot = (
        MorningSnapshot.objects.select_for_update()
        .filter(calendar_day=calendar_day)
        .first()
    )
    if snapshot is not None and not force:
        logger.info("Morning snapshot for %s already exists (id=%s)", morning_date, snapshot.pk)
        return snapshot
    if snapshot is not None and snapshot.state == InspectionState.CLOSED:
        raise ValidationError("The snapshot for this day is closed and cannot be regenerated.")

    if snapshot is None:
        snapshot = MorningSnapshot.objects.create(
            calendar_day=calendar_day,
            month=month,
            cutoff_at=cutoff,
            generated_at=timezone.now(),
            generated_by=actor,
            opened_by=actor,
        )

    evening_results = {
        result.student_id: result
        for result in EveningCheckResult.objects.filter(
            session__calendar_day__date=evening_date
        ).select_related("status")
    }

    students = (
        StudentProfile.objects.active()
        .select_related("group")
        .prefetch_related("room_assignments__room")
    )

    existing_items = {item.student_id: item for item in snapshot.items.all()}
    new_items = []

    for student in students:
        if student.pk in existing_items:
            continue  # never rewrite an item that already exists

        evening_result = evening_results.get(student.pk)
        evening_inside = (
            evening_result.status.counts_as_inside if evening_result else None
        )
        cutoff_status = status_at(student, cutoff)
        cutoff_inside = cutoff_status.counts_as_inside if cutoff_status else None

        calculated = calculate_morning_result(
            evening_inside=evening_inside, cutoff_inside=cutoff_inside
        )
        room = student.current_room

        new_items.append(
            MorningSnapshotItem(
                snapshot=snapshot,
                student=student,
                student_name=student.full_name,
                room_number=(evening_result.room_number if evening_result else "")
                or (room.number if room else ""),
                floor=room.floor if room else None,
                school_class=student.school_class,
                group_name=student.group.name if student.group else "",
                evening_status_code=evening_result.status.code if evening_result else "",
                evening_status_label=evening_result.status.label if evening_result else "",
                cutoff_status_code=cutoff_status.code if cutoff_status else "",
                cutoff_status_label=cutoff_status.label if cutoff_status else "",
                calculated_result=calculated,
                final_result=calculated,
            )
        )

    if new_items:
        MorningSnapshotItem.objects.bulk_create(new_items, ignore_conflicts=True)

    record_audit(
        user=actor,
        action=AuditAction.CREATE,
        target=snapshot,
        new_value={"date": str(morning_date), "items": len(new_items)},
        note="morning snapshot generated",
    )
    logger.info("Morning snapshot %s: %s new item(s)", morning_date, len(new_items))
    return snapshot


@transaction.atomic
def review_morning_item(*, item, actor, final_result=None, note=None):
    """Record a staff correction to one snapshot item.

    The calculated value is never overwritten - the correction lands in
    ``final_result`` so the original computation stays auditable.
    """
    _require_module()
    if not actor.has_capability(Capability.EDIT_MORNING_CHECK):
        raise PermissionDenied("Missing capability to review the morning check.")

    item = MorningSnapshotItem.objects.select_for_update().select_related("snapshot").get(pk=item.pk)
    if item.snapshot.state == InspectionState.CLOSED:
        raise ValidationError("This morning inspection is closed.")

    before = {"final_result": item.final_result, "note": item.note}

    if final_result is not None:
        if final_result not in MorningResult.values:
            raise ValidationError(f"Unknown morning result '{final_result}'.")
        item.final_result = final_result
    if note is not None:
        item.note = note[:255]

    item.is_reviewed = True
    item.reviewed_by = actor
    item.reviewed_at = timezone.now()
    item.save(
        update_fields=[
            "final_result",
            "note",
            "is_reviewed",
            "reviewed_by",
            "reviewed_at",
            "updated_at",
        ]
    )

    record_audit(
        user=actor,
        action=AuditAction.UPDATE,
        target=item,
        old_value=before,
        new_value={"final_result": item.final_result, "note": item.note},
    )
    return item


@transaction.atomic
def close_morning_snapshot(*, snapshot, actor, allow_unreviewed=True):
    _require_module()
    if not actor.has_capability(Capability.EDIT_MORNING_CHECK):
        raise PermissionDenied("Missing capability to close the morning check.")

    snapshot = MorningSnapshot.objects.select_for_update().get(pk=snapshot.pk)
    if snapshot.state == InspectionState.CLOSED:
        return snapshot

    unreviewed = snapshot.items.filter(is_reviewed=False).count()
    if unreviewed and not allow_unreviewed:
        raise ValidationError(f"{unreviewed} item(s) are still unreviewed.")

    snapshot.state = InspectionState.CLOSED
    snapshot.closed_by = actor
    snapshot.closed_at = timezone.now()
    snapshot.save(update_fields=["state", "closed_by", "closed_at", "updated_at"])
    record_audit(
        user=actor, action=AuditAction.CLOSE, target=snapshot, new_value={"unreviewed": unreviewed}
    )
    return snapshot


@transaction.atomic
def reopen_morning_snapshot(*, snapshot, actor, reason=""):
    _require_module()
    if not actor.has_capability(Capability.REOPEN_MORNING_CHECK):
        raise PermissionDenied("Missing capability to reopen the morning check.")

    snapshot = MorningSnapshot.objects.select_for_update().get(pk=snapshot.pk)
    if snapshot.state == InspectionState.OPEN:
        return snapshot

    snapshot.state = InspectionState.OPEN
    snapshot.reopened_by = actor
    snapshot.reopened_at = timezone.now()
    snapshot.reopen_reason = reason[:255]
    snapshot.save(
        update_fields=["state", "reopened_by", "reopened_at", "reopen_reason", "updated_at"]
    )
    record_audit(user=actor, action=AuditAction.REOPEN, target=snapshot, note=reason)
    return snapshot


def morning_summary(snapshot):
    from django.db.models import Count, F

    counts = dict(
        snapshot.items.values_list("final_result")
        .annotate(n=Count("id"))
        .values_list("final_result", "n")
    )
    return {
        "total": sum(counts.values()),
        "counts": counts,
        "reviewed": snapshot.items.filter(is_reviewed=True).count(),
        "corrected": snapshot.items.exclude(final_result=F("calculated_result")).count(),
        "unknown": counts.get(MorningResult.UNKNOWN, 0),
    }


def current_presence_map(student_ids):
    return {
        p.student_id: p
        for p in StudentPresence.objects.filter(student_id__in=student_ids).select_related(
            "status"
        )
    }

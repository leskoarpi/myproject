"""Evening presence inspection workflow (spec sections 16-18).

Concurrency here is real: two teachers may open the same floor at once. The
session row is locked with ``select_for_update`` for every state change, and a
soft edit-lock (``locked_by``/``locked_at``) tells the second person who is
already working on that floor. The soft lock is advisory; correctness comes
from the row lock and the per-result unique constraint.
"""

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.accounts.capabilities import Capability
from apps.audit.services import AuditAction, record_audit
from apps.core.models import ModuleKey, SystemModule
from apps.presence.models import PresenceSource, StatusType
from apps.presence.services import change_student_presence
from apps.rooms.services import students_on_floor

from ..models import EveningCheckResult, EveningCheckSession, InspectionState


class SessionLocked(PermissionDenied):
    """Raised when someone else holds the edit lock on a session."""


def _require_module():
    if not SystemModule.is_module_enabled(ModuleKey.EVENING_CHECK):
        raise PermissionDenied("The evening check module is disabled.")


@transaction.atomic
def open_evening_session(*, calendar_day, floor, actor):
    """Open (or return) the session for one floor on one day."""
    _require_module()
    if not actor.has_capability(Capability.EDIT_EVENING_CHECK):
        raise PermissionDenied("Missing capability to run the evening check.")
    if not calendar_day.evening_check_required:
        raise ValidationError("No evening check is required on this day.")

    session, created = EveningCheckSession.objects.get_or_create(
        calendar_day=calendar_day,
        floor=floor,
        defaults={
            "opened_by": actor,
            "opened_at": timezone.now(),
            "last_activity_at": timezone.now(),
        },
    )
    if created:
        record_audit(user=actor, action=AuditAction.CREATE, target=session)
    return session


@transaction.atomic
def acquire_session_lock(*, session, actor, model=EveningCheckSession):
    """Take the advisory edit lock, or fail if someone else holds a fresh one."""
    session = model.objects.select_for_update().get(pk=session.pk)
    if session.state != InspectionState.OPEN:
        raise ValidationError("This session is closed.")

    timeout = settings.INSPECTION_LOCK_TIMEOUT_SECONDS
    holder_id = session.locked_by_id
    if holder_id and holder_id != actor.pk and not session.lock_is_stale(timeout):
        raise SessionLocked(
            f"{session.lock_holder_display()} is currently editing this session."
        )

    session.locked_by = actor
    session.locked_at = timezone.now()
    session.last_activity_at = timezone.now()
    session.save(update_fields=["locked_by", "locked_at", "last_activity_at", "updated_at"])
    return session


@transaction.atomic
def release_session_lock(*, session, actor, model=EveningCheckSession):
    session = model.objects.select_for_update().get(pk=session.pk)
    if session.locked_by_id in (None, actor.pk) or actor.has_capability(
        Capability.REOPEN_EVENING_CHECK
    ):
        session.locked_by = None
        session.locked_at = None
        session.save(update_fields=["locked_by", "locked_at", "updated_at"])
    return session


@transaction.atomic
def save_evening_result(*, session, student, status, actor, note="", sync_presence=True):
    """Record one student's evening result.

    The result is inspection data first; it also moves the student's *current*
    presence, sourced as ``evening_check`` so the history stays attributable.
    """
    _require_module()
    if not actor.has_capability(Capability.EDIT_EVENING_CHECK):
        raise PermissionDenied("Missing capability to edit the evening check.")

    session = EveningCheckSession.objects.select_for_update().get(pk=session.pk)
    if session.state != InspectionState.OPEN:
        raise ValidationError("This session is closed; reopen it before editing.")

    timeout = settings.INSPECTION_LOCK_TIMEOUT_SECONDS
    if (
        session.locked_by_id
        and session.locked_by_id != actor.pk
        and not session.lock_is_stale(timeout)
    ):
        raise SessionLocked(f"{session.lock_holder_display()} is editing this session.")

    if isinstance(status, str):
        resolved = StatusType.objects.filter(code=status, is_active=True).first()
        if resolved is None:
            raise ValidationError(f"Unknown status '{status}'.")
        status = resolved

    room = student.current_room
    if room is None or room.floor != session.floor:
        raise ValidationError(
            f"{student.full_name} is not assigned to a room on floor {session.floor}."
        )

    result, _ = EveningCheckResult.objects.update_or_create(
        session=session,
        student=student,
        defaults={
            "status": status,
            "room_number": room.number,
            "note": note[:255],
            "recorded_by": actor,
            "recorded_at": timezone.now(),
        },
    )

    if sync_presence:
        change_student_presence(
            student=student,
            new_status=status,
            actor=actor,
            reason=note or "Esti ellenőrzés",
            source=PresenceSource.EVENING_CHECK,
            enforce_permissions=False,
        )

    session.locked_by = actor
    session.locked_at = timezone.now()
    session.last_activity_at = timezone.now()
    session.save(update_fields=["locked_by", "locked_at", "last_activity_at", "updated_at"])
    return result


def evening_session_progress(session):
    """How far along one floor's inspection is."""
    expected = students_on_floor(session.floor).count()
    recorded = session.results.count()
    inside = session.results.filter(status__counts_as_inside=True).count()
    return {
        "expected": expected,
        "recorded": recorded,
        "missing": max(expected - recorded, 0),
        "inside": inside,
        "outside": recorded - inside,
        "complete": expected > 0 and recorded >= expected,
        "percent": int(round(100 * recorded / expected)) if expected else 0,
    }


@transaction.atomic
def close_evening_session(*, session, actor, allow_incomplete=False):
    _require_module()
    if not actor.has_capability(Capability.EDIT_EVENING_CHECK):
        raise PermissionDenied("Missing capability to close the evening check.")

    session = EveningCheckSession.objects.select_for_update().get(pk=session.pk)
    if session.state == InspectionState.CLOSED:
        return session

    progress = evening_session_progress(session)
    if progress["missing"] and not allow_incomplete:
        raise ValidationError(
            f"{progress['missing']} student(s) have no result yet on this floor."
        )

    session.state = InspectionState.CLOSED
    session.closed_by = actor
    session.closed_at = timezone.now()
    session.locked_by = None
    session.locked_at = None
    session.save(
        update_fields=[
            "state",
            "closed_by",
            "closed_at",
            "locked_by",
            "locked_at",
            "updated_at",
        ]
    )
    record_audit(
        user=actor,
        action=AuditAction.CLOSE,
        target=session,
        new_value=progress,
    )
    return session


@transaction.atomic
def reopen_evening_session(*, session, actor, reason=""):
    _require_module()
    if not actor.has_capability(Capability.REOPEN_EVENING_CHECK):
        raise PermissionDenied("Missing capability to reopen the evening check.")

    session = EveningCheckSession.objects.select_for_update().get(pk=session.pk)
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


def evening_overview(calendar_day):
    """Per-floor status line for the staff dashboard."""
    from apps.rooms.services import floors

    sessions = {
        s.floor: s
        for s in EveningCheckSession.objects.filter(calendar_day=calendar_day).annotate(
            result_count=Count("results"),
            inside_count=Count("results", filter=Q(results__status__counts_as_inside=True)),
        )
    }
    overview = []
    for floor in floors():
        session = sessions.get(floor)
        expected = students_on_floor(floor).count()
        recorded = getattr(session, "result_count", 0) if session else 0
        overview.append(
            {
                "floor": floor,
                "session": session,
                "expected": expected,
                "recorded": recorded,
                "state": session.state if session else None,
                "percent": int(round(100 * recorded / expected)) if expected else 0,
            }
        )
    return overview

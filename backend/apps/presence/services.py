"""Presence services.

``change_student_presence`` is the *only* supported way to move a student
between statuses. It updates the current row and appends the immutable event
in one transaction (spec sections 12-14, 72).
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.capabilities import Capability, Role

from .models import PresenceEvent, PresenceSource, StatusType, StudentPresence

# Which source a status change gets when a person triggers it directly.
ROLE_SOURCES = {
    Role.STUDENT: PresenceSource.STUDENT,
    Role.TEACHER: PresenceSource.TEACHER,
    Role.PORTER: PresenceSource.PORTER,
    Role.MANAGEMENT: PresenceSource.ADMIN,
    Role.ADMIN: PresenceSource.ADMIN,
}


def source_for_actor(actor):
    if actor is None or not getattr(actor, "is_authenticated", False):
        return PresenceSource.SYSTEM
    return ROLE_SOURCES.get(actor.role, PresenceSource.SYSTEM)


def can_change_presence_of(actor, student):
    """Object-level check: may ``actor`` move *this* student's status?"""
    if actor is None or not actor.is_authenticated:
        return False

    own_profile = getattr(actor, "student_profile", None)
    if own_profile is not None and own_profile.pk == student.pk:
        # Students act on themselves only, and only via self-service.
        return actor.has_capability(Capability.SELF_PRESENCE)

    if not actor.has_capability(Capability.EDIT_PRESENCE):
        return False

    from apps.students.selectors import student_queryset_for_user

    return student_queryset_for_user(actor).filter(pk=student.pk).exists()


def ensure_presence_row(student):
    """Every student has a current-presence row; create it lazily."""
    presence = StudentPresence.objects.filter(student=student).first()
    if presence is not None:
        return presence
    default_status = StatusType.default()
    if default_status is None:
        raise ValidationError("No status types are configured.")
    return StudentPresence.objects.create(
        student=student,
        status=default_status,
        source=PresenceSource.SYSTEM,
        changed_at=timezone.now(),
    )


@transaction.atomic
def change_student_presence(
    *,
    student,
    new_status,
    actor=None,
    reason="",
    source=None,
    expected_return_at=None,
    enforce_permissions=True,
    when=None,
):
    """Move ``student`` to ``new_status`` and append a history event.

    Returns the created :class:`PresenceEvent`, or ``None`` when the status was
    already the requested one (no-op changes are not recorded).
    """
    if enforce_permissions and not can_change_presence_of(actor, student):
        raise PermissionDenied("You may not change this student's presence.")

    if isinstance(new_status, str):
        resolved = StatusType.objects.filter(code=new_status, is_active=True).first()
        if resolved is None:
            raise ValidationError(f"Unknown or inactive status '{new_status}'.")
        new_status = resolved
    elif not new_status.is_active:
        raise ValidationError(f"Status '{new_status.code}' is not active.")

    if new_status.requires_note and not reason.strip():
        raise ValidationError(f"Status '{new_status.label}' requires a note.")

    source = source or source_for_actor(actor)
    when = when or timezone.now()

    presence = (
        StudentPresence.objects.select_for_update().filter(student=student).first()
    )
    if presence is None:
        presence = ensure_presence_row(student)
        presence = StudentPresence.objects.select_for_update().get(pk=presence.pk)

    old_status = presence.status
    if old_status_id_matches(old_status, new_status) and not reason:
        return None

    presence.status = new_status
    presence.note = reason[:255]
    presence.source = source
    presence.changed_at = when
    presence.changed_by = actor if getattr(actor, "pk", None) else None
    presence.expected_return_at = expected_return_at
    presence.save(
        update_fields=[
            "status",
            "note",
            "source",
            "changed_at",
            "changed_by",
            "expected_return_at",
            "updated_at",
        ]
    )

    return PresenceEvent.objects.create(
        student=student,
        old_status=old_status,
        new_status=new_status,
        reason=reason[:255],
        source=source,
        created_at=when,
        created_by=actor if getattr(actor, "pk", None) else None,
    )


def old_status_id_matches(old_status, new_status):
    return old_status is not None and old_status.pk == new_status.pk


def student_return(*, student, actor, reason=""):
    """The "Bejöttem" action (spec section 12)."""
    inside = StatusType.objects.filter(counts_as_inside=True, is_active=True).order_by(
        "sort_order"
    ).first()
    if inside is None:
        raise ValidationError("No 'inside' status is configured.")
    return change_student_presence(
        student=student, new_status=inside, actor=actor, reason=reason
    )


def student_leave(*, student, actor, status_code, reason=""):
    """The "Kimentem" action with a chosen reason (spec section 12)."""
    status = StatusType.objects.filter(
        code=status_code, is_active=True, student_selectable=True
    ).first()
    if status is None:
        raise ValidationError("That reason is not selectable.")
    if status.counts_as_inside:
        raise ValidationError("That status is not a leaving reason.")
    return change_student_presence(
        student=student, new_status=status, actor=actor, reason=reason
    )


def presence_summary(student_queryset):
    """Counts per status for a dashboard, in one query."""
    from django.db.models import Count

    rows = (
        StudentPresence.objects.filter(student__in=student_queryset)
        .values("status__code", "status__label", "status__color", "status__counts_as_inside")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    inside = sum(r["count"] for r in rows if r["status__counts_as_inside"])
    outside = sum(r["count"] for r in rows if not r["status__counts_as_inside"])
    total = student_queryset.count()
    return {
        "rows": list(rows),
        "inside": inside,
        "outside": outside,
        "total": total,
        "unknown": max(total - inside - outside, 0),
    }

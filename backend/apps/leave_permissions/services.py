"""Leave permission services (spec sections 38-40)."""

import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.capabilities import Capability
from apps.audit.services import AuditAction, record_audit
from apps.core.models import ModuleKey, SystemModule

from .models import (
    HistoryAction,
    LeavePermission,
    LeavePermissionHistory,
    PermissionStatus,
    PermissionType,
)

logger = logging.getLogger(__name__)


def _require_module():
    if not SystemModule.is_module_enabled(ModuleKey.LEAVE_PERMISSIONS):
        raise PermissionDenied("The leave permission module is disabled.")


def leave_permission_queryset_for_user(user):
    base = LeavePermission.objects.select_related("student", "granted_by")
    if not user.is_authenticated:
        return base.none()
    if user.has_capability(Capability.VIEW_LEAVE_PERMISSIONS):
        from apps.students.selectors import student_queryset_for_user

        return base.filter(student__in=student_queryset_for_user(user))
    if user.has_capability(Capability.VIEW_OWN_LEAVE_PERMISSION):
        profile = getattr(user, "student_profile", None)
        return base.filter(student=profile) if profile else base.none()
    return base.none()


def _write_history(permission, action, before, actor, note=""):
    return LeavePermissionHistory.objects.create(
        student=permission.student,
        action=action,
        previous_status=before["status"],
        previous_value=before["value"],
        new_status=permission.status,
        new_value=permission.value,
        note=note[:255],
        created_by=actor if getattr(actor, "pk", None) else None,
    )


def _snapshot(permission):
    return {"status": permission.status, "value": permission.value}


@transaction.atomic
def grant_leave_permission(
    *, student, actor, permission_type=PermissionType.STANDING, value="", expires_at=None, note=""
):
    """Grant or update a student's leave permission."""
    _require_module()
    if not actor.has_capability(Capability.MANAGE_LEAVE_PERMISSIONS):
        raise PermissionDenied("Missing capability to manage leave permissions.")

    from apps.students.selectors import student_queryset_for_user

    if not student_queryset_for_user(actor).filter(pk=student.pk).exists():
        raise PermissionDenied("This student is outside your scope.")

    if expires_at is not None and expires_at <= timezone.now():
        raise ValidationError("The expiry must be in the future.")
    if permission_type not in PermissionType.values:
        raise ValidationError(f"Unknown permission type '{permission_type}'.")

    permission, _ = LeavePermission.objects.select_for_update().get_or_create(student=student)
    before = _snapshot(permission)
    was_empty = permission.status in {PermissionStatus.EMPTY, PermissionStatus.EXPIRED}

    permission.status = PermissionStatus.ACTIVE
    permission.permission_type = permission_type
    permission.value = value[:255]
    permission.granted_at = timezone.now()
    permission.granted_by = actor
    permission.expires_at = expires_at
    permission.save()

    _write_history(
        permission,
        HistoryAction.GRANT if was_empty else HistoryAction.UPDATE,
        before,
        actor,
        note,
    )
    record_audit(
        user=actor,
        action=AuditAction.UPDATE,
        target=permission,
        old_value=before,
        new_value=_snapshot(permission),
        note=note,
    )
    return permission


@transaction.atomic
def revoke_leave_permission(*, student, actor, note=""):
    _require_module()
    if not actor.has_capability(Capability.MANAGE_LEAVE_PERMISSIONS):
        raise PermissionDenied("Missing capability to manage leave permissions.")

    permission = LeavePermission.objects.select_for_update().filter(student=student).first()
    if permission is None or permission.status != PermissionStatus.ACTIVE:
        raise ValidationError("This student has no active leave permission.")

    before = _snapshot(permission)
    permission.status = PermissionStatus.REVOKED
    permission.expires_at = None
    permission.save(update_fields=["status", "expires_at", "updated_at"])

    _write_history(permission, HistoryAction.REVOKE, before, actor, note)
    record_audit(
        user=actor,
        action=AuditAction.UPDATE,
        target=permission,
        old_value=before,
        new_value=_snapshot(permission),
        note=note or "revoked",
    )
    return permission


@transaction.atomic
def expire_leave_permissions(now=None):
    """Expire every permission whose expiry has passed. Idempotent.

    Runs from Celery Beat daily; running it twice expires nothing extra
    because only ACTIVE rows are selected (spec section 40).
    """
    now = now or timezone.now()
    expired = 0

    due = (
        LeavePermission.objects.select_for_update(skip_locked=True)
        .filter(status=PermissionStatus.ACTIVE, expires_at__isnull=False, expires_at__lte=now)
        .select_related("student")
    )
    for permission in due:
        before = _snapshot(permission)
        permission.status = PermissionStatus.EXPIRED
        permission.save(update_fields=["status", "updated_at"])
        _write_history(permission, HistoryAction.EXPIRE, before, None, "automatic expiry")
        record_audit(
            user=None,
            action=AuditAction.UPDATE,
            target=permission,
            old_value=before,
            new_value=_snapshot(permission),
            note="automatic expiry",
        )
        expired += 1

    if expired:
        logger.info("Expired %s leave permission(s)", expired)
    return expired

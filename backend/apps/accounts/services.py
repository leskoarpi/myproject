"""Privilege management services.

Two things a manager can change on an account: its role, and a per-user
override on top of that role (``extra_capabilities`` grants something the
role doesn't have; ``revoked_capabilities`` takes away something it does -
see :func:`apps.accounts.capabilities.user_capabilities`). Both go through
:mod:`apps.accounts.selectors` for the object-level and role-ceiling checks,
and both are audited (spec sections 47, 55, 58).
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.audit.services import AuditAction, record_audit

from .capabilities import Capability
from .selectors import assignable_roles_for, can_manage_user, grantable_capabilities_for


def _snapshot(user):
    return {
        "role": user.role,
        "extra_capabilities": sorted(user.extra_capabilities or []),
        "revoked_capabilities": sorted(user.revoked_capabilities or []),
    }


@transaction.atomic
def set_user_role(*, target, actor, role):
    if not actor.has_capability(Capability.MANAGE_USERS):
        raise PermissionDenied("Nincs jogosultságod felhasználókat kezelni.")
    if not can_manage_user(actor, target):
        raise PermissionDenied("Ezt a fiókot nem kezelheted.")

    allowed = {value for value, _ in assignable_roles_for(actor)}
    if role not in allowed:
        raise ValidationError("Ezt a szerepkört nem oszthatod ki.")

    target = type(target).objects.select_for_update().get(pk=target.pk)
    before = _snapshot(target)
    if before["role"] == role:
        return target

    target.role = role
    target.refresh_capabilities()
    target.save(update_fields=["role"])

    record_audit(
        user=actor,
        action=AuditAction.UPDATE,
        target=target,
        old_value=before,
        new_value=_snapshot(target),
        note="role changed",
    )
    return target


@transaction.atomic
def set_user_capabilities(*, target, actor, extra, revoked):
    """Replace the account's capability overrides wholesale.

    ``extra``/``revoked`` are the full desired sets, not a delta - the edit
    screen submits the whole capability table each time.
    """
    if not actor.has_capability(Capability.MANAGE_USERS):
        raise PermissionDenied("Nincs jogosultságod felhasználókat kezelni.")
    if not can_manage_user(actor, target):
        raise PermissionDenied("Ezt a fiókot nem kezelheted.")

    grantable = set(grantable_capabilities_for(actor))
    extra = set(extra) & grantable
    revoked = set(revoked) & grantable
    # A capability cannot be both forced on and forced off at once; "off"
    # wins, since revoking is the more conservative choice when a submitted
    # form somehow asks for both.
    extra -= revoked

    target = type(target).objects.select_for_update().get(pk=target.pk)
    before = _snapshot(target)

    target.extra_capabilities = sorted(extra)
    target.revoked_capabilities = sorted(revoked)
    target.refresh_capabilities()
    target.save(update_fields=["extra_capabilities", "revoked_capabilities"])

    after = _snapshot(target)
    if after == before:
        return target

    record_audit(
        user=actor,
        action=AuditAction.UPDATE,
        target=target,
        old_value=before,
        new_value=after,
        note="capability overrides changed",
    )
    return target

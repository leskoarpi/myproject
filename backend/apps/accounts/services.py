"""Privilege management services.

Two things a manager can change on an account: its role, and a per-user
override on top of that role (``extra_capabilities`` grants something the
role doesn't have; ``revoked_capabilities`` takes away something it does -
see :func:`apps.accounts.capabilities.user_capabilities`). Both go through
:mod:`apps.accounts.selectors` for the object-level and role-ceiling checks,
and both are audited (spec sections 47, 55, 58).
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q

from apps.audit.services import AuditAction, record_audit

from .capabilities import Capability, Role
from .selectors import (
    assignable_roles_for,
    can_delete_user,
    can_manage_user,
    grantable_capabilities_for,
    is_student_account,
)

User = get_user_model()


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


@transaction.atomic
def delete_user_permanently(*, target, actor, confirmation_username):
    """Hard-delete an account. Irreversible - unlike archiving a student or
    disabling an account, there is no undo. Admin-only, never self-service,
    never a student (see :func:`apps.accounts.selectors.is_student_account`),
    and never the last admin standing, or nobody could use this screen to fix
    that mistake afterward.

    Every foreign key from historical records to ``User`` is ``SET_NULL``
    (see the model comments this decision is drawn from), so presence
    events, room checks, audit entries and the rest all survive with their
    "who did this" attribution cleared, not deleted. A ``Teacher`` profile
    cascades away with its account, since it carries no history of its own.
    """
    # Checked before the general scope check: a student account is never
    # deletable by anyone, and that specific reason is more useful to the
    # operator than a generic "you may not manage this account" - without
    # this ordering, can_delete_user() already filters students out first
    # and the friendlier message never surfaces.
    if is_student_account(target):
        raise ValidationError(
            "Diákfiók nem törölhető véglegesen. Archiváld a diákot az adatlapján."
        )

    if not can_delete_user(actor, target):
        raise PermissionDenied("Ezt a fiókot nem törölheted.")

    if confirmation_username != target.username:
        raise ValidationError("A megerősítéshez pontosan a felhasználónevet kell beírni.")

    target = User.objects.select_for_update().get(pk=target.pk)
    is_last_admin = target.is_superuser or target.role == Role.ADMIN
    if is_last_admin:
        other_admin_exists = (
            User.objects.filter(is_active=True)
            .filter(Q(is_superuser=True) | Q(role=Role.ADMIN))
            .exclude(pk=target.pk)
            .exists()
        )
        if not other_admin_exists:
            raise ValidationError(
                "Ez az utolsó adminisztrátori fiók - nem törölhető, mert senki "
                "sem tudná ezt a döntést visszavonni."
            )

    snapshot = {
        "username": target.username,
        "email": target.email,
        "role": target.role,
        "display_name": target.display_name,
    }
    target_id = target.pk
    target.delete()

    record_audit(
        user=actor,
        action=AuditAction.DELETE,
        target_type="accounts.User",
        target_id=str(target_id),
        target_repr=snapshot["display_name"],
        old_value=snapshot,
        note="account permanently deleted",
    )
    return snapshot

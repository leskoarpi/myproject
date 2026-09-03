"""Object-level scope for user/privilege management.

``MANAGE_USERS`` answers "may this user manage accounts at all?" - the
capability check. This module answers "*which* accounts, and to *what*
roles?" (spec section 58): admin is the one tier nobody but another admin may
touch, per spec section 3.2 ("management should not automatically receive
system-owner/destructive permissions"). Management can manage everyone below
that line - other management accounts included - but never an admin account,
never promotes anyone to Admin, and can never hand out ``DELETE_ALL_DATA``.

Permanent deletion is a further, admin-only step on top of all that: see
``can_delete_user`` and ``apps.accounts.services.delete_user_permanently``.
"""

from django.contrib.auth import get_user_model

from .capabilities import Capability, GRANTABLE_CAPABILITIES, Role

User = get_user_model()

# Capabilities manageable through the privilege screen. DELETE_ALL_DATA stays
# out of everyone's reach here, admin included - that capability only ever
# comes from the Admin role itself, never from a one-off grant.
MANAGEABLE_CAPABILITIES = GRANTABLE_CAPABILITIES


def is_admin_tier(user):
    return user.is_superuser or user.role == Role.ADMIN


def users_manageable_by(actor):
    """Accounts ``actor`` may view/edit privileges for."""
    if not actor or not actor.is_authenticated:
        return User.objects.none()
    if not actor.has_capability(Capability.MANAGE_USERS):
        return User.objects.none()

    base = User.objects.all()
    if is_admin_tier(actor):
        return base
    # Management: everyone except the admin tier - and never themselves,
    # to avoid an accidental self-lockout bypassing the normal admin path.
    return base.exclude(is_superuser=True).exclude(role=Role.ADMIN).exclude(pk=actor.pk)


def can_manage_user(actor, target):
    return users_manageable_by(actor).filter(pk=target.pk).exists()


def is_student_account(user):
    """Students are never permanently deletable - archive instead.

    ``StudentProfile.user`` is ``on_delete=PROTECT`` precisely so this can't
    happen by accident (spec section 7: student records must not be
    physically deleted during normal operation); this just gives a clear
    message up front instead of an unhandled ``ProtectedError``.
    """
    return hasattr(user, "student_profile")


def can_delete_user(actor, target):
    """Permanent deletion, unlike privilege editing, is admin-only and never
    self-service - archiving/disabling remain the reversible options for
    everyone else. See :func:`apps.accounts.services.delete_user_permanently`
    for the transaction-time checks (last-admin guard) this does not repeat.
    """
    if not actor or not actor.is_authenticated:
        return False
    if not actor.has_capability(Capability.DELETE_USERS):
        return False
    if target.pk == actor.pk:
        return False
    if is_student_account(target):
        return False
    return can_manage_user(actor, target)


def assignable_roles_for(actor):
    """Which roles ``actor`` may set on a managed account."""
    if is_admin_tier(actor):
        return list(Role.choices)
    return [(value, label) for value, label in Role.choices if value != Role.ADMIN]


def grantable_capabilities_for(actor):
    """Capabilities ``actor`` may explicitly grant or revoke on an account.

    Same list for everyone who reaches this screen: DELETE_ALL_DATA is
    excluded up front by ``MANAGEABLE_CAPABILITIES``, and the admin-only role
    ceiling is enforced separately, by role assignment rather than by
    capability grant.
    """
    return MANAGEABLE_CAPABILITIES

"""Object-level scope for user/privilege management.

``MANAGE_USERS`` answers "may this user manage accounts at all?" - the
capability check. This module answers "*which* accounts, and to *what*
roles?" (spec section 58): admin is the one tier nobody but another admin may
touch, per spec section 3.2 ("management should not automatically receive
system-owner/destructive permissions"). Management can manage everyone below
that line - other management accounts included - but never an admin account,
never promotes anyone to Admin, and can never hand out ``DELETE_ALL_DATA``.
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

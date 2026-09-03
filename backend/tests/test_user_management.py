"""Privilege management: admin and management can edit any (non-admin, for
management) account's role and per-capability overrides (spec 57-58).

The admin/management line drawn everywhere else in this app - "management
should not automatically receive system-owner permissions" (spec 3.2) -
applies here too: management can manage everyone below that line, including
other management accounts, but never an admin account, never promotes anyone
to Admin, and can never hand out DELETE_ALL_DATA.
"""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.accounts.capabilities import Capability, Role
from apps.accounts.selectors import (
    assignable_roles_for,
    can_manage_user,
    users_manageable_by,
)
from apps.accounts.services import set_user_capabilities, set_user_role

from . import factories as f

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    return {
        "admin": f.make_user(Role.ADMIN, is_superuser=True, is_staff=True),
        "management": f.make_user(Role.MANAGEMENT),
        "other_management": f.make_user(Role.MANAGEMENT),
        "teacher": f.make_teacher().user,
        "porter": f.make_user(Role.PORTER),
        "student": f.make_student().user,
    }


# --------------------------------------------------------------------------
# Capability grant itself
# --------------------------------------------------------------------------


def test_admin_and_management_both_hold_manage_users(world):
    assert world["admin"].has_capability(Capability.MANAGE_USERS)
    assert world["management"].has_capability(Capability.MANAGE_USERS)


def test_nobody_else_holds_it(world):
    for role_user in ("teacher", "porter", "student"):
        assert not world[role_user].has_capability(Capability.MANAGE_USERS)


# --------------------------------------------------------------------------
# Selectors: who can manage whom, and assign which roles
# --------------------------------------------------------------------------


def test_admin_can_manage_everyone_including_other_admins_and_self(world):
    other_admin = f.make_user(Role.ADMIN, is_superuser=True)
    scope = users_manageable_by(world["admin"])
    assert other_admin in scope
    assert world["admin"] in scope
    assert world["management"] in scope
    assert world["student"] in scope


def test_management_cannot_manage_admin_accounts(world):
    assert not can_manage_user(world["management"], world["admin"])
    assert world["admin"] not in users_manageable_by(world["management"])


def test_management_cannot_manage_their_own_account(world):
    assert not can_manage_user(world["management"], world["management"])


def test_management_can_manage_other_management_teachers_porters_students(world):
    scope = users_manageable_by(world["management"])
    assert world["other_management"] in scope
    assert world["teacher"] in scope
    assert world["porter"] in scope
    assert world["student"] in scope


def test_admin_role_is_assignable_only_by_admin(world):
    admin_roles = {value for value, _ in assignable_roles_for(world["admin"])}
    management_roles = {value for value, _ in assignable_roles_for(world["management"])}
    assert Role.ADMIN in admin_roles
    assert Role.ADMIN not in management_roles


# --------------------------------------------------------------------------
# Services: role changes
# --------------------------------------------------------------------------


def test_management_can_promote_a_teacher_to_management(world):
    updated = set_user_role(target=world["teacher"], actor=world["management"], role=Role.MANAGEMENT)
    assert updated.role == Role.MANAGEMENT


def test_management_cannot_promote_anyone_to_admin(world):
    with pytest.raises(ValidationError):
        set_user_role(target=world["teacher"], actor=world["management"], role=Role.ADMIN)


def test_management_cannot_touch_an_admin_account(world):
    with pytest.raises(PermissionDenied):
        set_user_role(target=world["admin"], actor=world["management"], role=Role.PORTER)


def test_admin_can_promote_anyone_to_admin(world):
    updated = set_user_role(target=world["teacher"], actor=world["admin"], role=Role.ADMIN)
    assert updated.role == Role.ADMIN


def test_teacher_cannot_change_anyones_role(world):
    with pytest.raises(PermissionDenied):
        set_user_role(target=world["student"], actor=world["teacher"], role=Role.MANAGEMENT)


def test_role_change_is_audited(world):
    from apps.audit.models import AuditLog

    set_user_role(target=world["teacher"], actor=world["management"], role=Role.MANAGEMENT)
    assert AuditLog.objects.filter(target_id=str(world["teacher"].pk), note="role changed").exists()


# --------------------------------------------------------------------------
# Services: capability overrides
# --------------------------------------------------------------------------


def test_extra_capability_grant_takes_effect(world):
    set_user_capabilities(
        target=world["student"],
        actor=world["admin"],
        extra={Capability.VIEW_ROOMS},
        revoked=set(),
    )
    world["student"].refresh_from_db()
    world["student"].refresh_capabilities()
    assert world["student"].has_capability(Capability.VIEW_ROOMS)


def test_revocation_removes_a_role_default_capability(world):
    assert world["teacher"].has_capability(Capability.EDIT_PRESENCE)
    set_user_capabilities(
        target=world["teacher"],
        actor=world["management"],
        extra=set(),
        revoked={Capability.EDIT_PRESENCE},
    )
    world["teacher"].refresh_from_db()
    world["teacher"].refresh_capabilities()
    assert not world["teacher"].has_capability(Capability.EDIT_PRESENCE)


def test_revoked_wins_when_both_are_submitted_for_the_same_capability(world):
    set_user_capabilities(
        target=world["student"],
        actor=world["admin"],
        extra={Capability.VIEW_ROOMS},
        revoked={Capability.VIEW_ROOMS},
    )
    world["student"].refresh_from_db()
    assert Capability.VIEW_ROOMS not in world["student"].extra_capabilities
    assert Capability.VIEW_ROOMS in world["student"].revoked_capabilities


def test_delete_all_data_cannot_be_granted_through_this_mechanism(world):
    """Not even the admin actor can hand this one out as a one-off grant -
    it only ever comes from the Admin role itself."""
    set_user_capabilities(
        target=world["student"],
        actor=world["admin"],
        extra={Capability.DELETE_ALL_DATA},
        revoked=set(),
    )
    world["student"].refresh_from_db()
    assert Capability.DELETE_ALL_DATA not in world["student"].extra_capabilities


def test_management_cannot_set_capabilities_on_an_admin(world):
    with pytest.raises(PermissionDenied):
        set_user_capabilities(
            target=world["admin"], actor=world["management"], extra=set(), revoked=set()
        )


def test_capability_change_is_audited(world):
    from apps.audit.models import AuditLog

    set_user_capabilities(
        target=world["student"], actor=world["admin"], extra={Capability.VIEW_ROOMS}, revoked=set()
    )
    assert AuditLog.objects.filter(
        target_id=str(world["student"].pk), note="capability overrides changed"
    ).exists()


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------


def test_user_list_reachable_by_admin_and_management(client, world):
    for actor in ("admin", "management"):
        client.force_login(world[actor])
        assert client.get(reverse("accounts:user_list")).status_code == 200


def test_user_list_hides_admin_accounts_from_management(client, world):
    client.force_login(world["management"])
    body = client.get(reverse("accounts:user_list")).content.decode()
    assert world["admin"].username not in body


def test_teacher_gets_403_on_the_user_list(client, world):
    client.force_login(world["teacher"])
    assert client.get(reverse("accounts:user_list")).status_code == 403


def test_management_gets_404_editing_an_admin_directly_by_url(client, world):
    client.force_login(world["management"])
    response = client.get(reverse("accounts:user_edit", kwargs={"user_id": world["admin"].pk}))
    assert response.status_code == 404


def test_edit_page_offers_no_admin_option_to_management(client, world):
    client.force_login(world["management"])
    body = client.get(
        reverse("accounts:user_edit", kwargs={"user_id": world["teacher"].pk})
    ).content.decode()
    assert "Adminisztrátor" not in body


def test_posting_a_role_and_capability_change_through_the_view(client, world):
    client.force_login(world["management"])
    response = client.post(
        reverse("accounts:user_edit", kwargs={"user_id": world["teacher"].pk}),
        {
            "role": Role.MANAGEMENT,
            "extra": [Capability.MANAGE_ROOMS],
            "revoked": [],
        },
        follow=True,
    )
    assert response.status_code == 200
    world["teacher"].refresh_from_db()
    assert world["teacher"].role == Role.MANAGEMENT
    assert Capability.MANAGE_ROOMS in world["teacher"].extra_capabilities


def test_posting_admin_role_through_the_view_is_rejected(client, world):
    client.force_login(world["management"])
    client.post(
        reverse("accounts:user_edit", kwargs={"user_id": world["teacher"].pk}),
        {"role": Role.ADMIN, "extra": [], "revoked": []},
    )
    world["teacher"].refresh_from_db()
    assert world["teacher"].role != Role.ADMIN


def test_toggle_active_disables_and_reenables_an_account(client, world):
    client.force_login(world["admin"])
    url = reverse("accounts:user_toggle_active", kwargs={"user_id": world["student"].pk})

    client.post(url)
    world["student"].refresh_from_db()
    assert world["student"].is_active is False

    client.post(url)
    world["student"].refresh_from_db()
    assert world["student"].is_active is True


def test_management_cannot_toggle_an_admin_account(client, world):
    client.force_login(world["management"])
    response = client.post(
        reverse("accounts:user_toggle_active", kwargs={"user_id": world["admin"].pk})
    )
    assert response.status_code == 404
    world["admin"].refresh_from_db()
    assert world["admin"].is_active is True

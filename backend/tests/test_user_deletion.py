"""Permanent user deletion, alongside archiving (spec 7, 49, 57-58).



Deliberately narrower than privilege management: admin-only (never
management, matching the DELETE_ALL_DATA precedent), never self-service,
never a student account (StudentProfile.user is PROTECT for exactly this
reason - archiving stays the only option there), and never the last admin
standing. Historical records attributed to the deleted account survive with
their "who did this" field cleared (SET_NULL), not deleted.
"""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.accounts.capabilities import Capability, Role
from apps.accounts.selectors import can_delete_user
from apps.accounts.services import delete_user_permanently
from apps.audit.models import AuditAction, AuditLog
from apps.people.models import Teacher

from . import factories as f

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    return {
        "admin": f.make_user(Role.ADMIN, is_superuser=True, is_staff=True),
        "other_admin": f.make_user(Role.ADMIN, is_superuser=True, is_staff=True),
        "management": f.make_user(Role.MANAGEMENT),
        "teacher_profile": f.make_teacher(),
        "porter": f.make_user(Role.PORTER),
        "student": f.make_student(),
    }


# --------------------------------------------------------------------------
# Capability grant
# --------------------------------------------------------------------------


def test_only_admin_holds_delete_users(world):
    assert world["admin"].has_capability(Capability.DELETE_USERS)
    assert not world["management"].has_capability(Capability.DELETE_USERS)


def test_delete_users_cannot_be_granted_as_a_one_off(world):
    """Matches DELETE_ALL_DATA: it only ever comes from the Admin role."""
    from apps.accounts.services import set_user_capabilities

    set_user_capabilities(
        target=world["management"],
        actor=world["admin"],
        extra={Capability.DELETE_USERS},
        revoked=set(),
    )
    world["management"].refresh_from_db()
    world["management"].refresh_capabilities()
    assert not world["management"].has_capability(Capability.DELETE_USERS)


# --------------------------------------------------------------------------
# Selector: who may be deleted by whom
# --------------------------------------------------------------------------


def test_admin_can_delete_a_teacher_porter_or_management_account(world):
    assert can_delete_user(world["admin"], world["teacher_profile"].user)
    assert can_delete_user(world["admin"], world["porter"])
    assert can_delete_user(world["admin"], world["management"])


def test_management_can_never_delete_anyone(world):
    assert not can_delete_user(world["management"], world["teacher_profile"].user)
    assert not can_delete_user(world["management"], world["porter"])


def test_nobody_can_delete_a_student_account(world):
    assert not can_delete_user(world["admin"], world["student"].user)


def test_nobody_can_delete_their_own_account(world):
    assert not can_delete_user(world["admin"], world["admin"])


# --------------------------------------------------------------------------
# Service: the actual deletion and its guards
# --------------------------------------------------------------------------


def test_deleting_a_teacher_removes_the_user_and_cascades_the_teacher_profile(world):
    teacher = world["teacher_profile"]
    user_pk = teacher.user.pk

    delete_user_permanently(
        target=teacher.user, actor=world["admin"], confirmation_username=teacher.user.username
    )

    from django.contrib.auth import get_user_model

    User = get_user_model()
    assert not User.objects.filter(pk=user_pk).exists()
    assert not Teacher.objects.filter(pk=teacher.pk).exists()


def test_wrong_confirmation_username_is_rejected(world):
    target = world["porter"]
    with pytest.raises(ValidationError):
        delete_user_permanently(
            target=target, actor=world["admin"], confirmation_username="not-the-username"
        )
    target.refresh_from_db()  # still exists


def test_a_student_account_is_refused_with_a_guiding_message(world):
    student_user = world["student"].user
    with pytest.raises(ValidationError, match="Archiváld"):
        delete_user_permanently(
            target=student_user, actor=world["admin"], confirmation_username=student_user.username
        )
    student_user.refresh_from_db()


def test_admin_cannot_delete_their_own_account(world):
    with pytest.raises(PermissionDenied):
        delete_user_permanently(
            target=world["admin"], actor=world["admin"], confirmation_username=world["admin"].username
        )


def test_management_cannot_delete_anyone_through_the_service(world):
    with pytest.raises(PermissionDenied):
        delete_user_permanently(
            target=world["porter"],
            actor=world["management"],
            confirmation_username=world["porter"].username,
        )


def test_the_last_admin_guard_blocks_deletion_when_reached(world, monkeypatch):
    """Defense-in-depth, tested in isolation.

    Under the real scoping rules this guard is only reachable via self-delete
    (only an admin-tier actor may manage an admin-tier target, and an actor
    who is admin-tier and active always counts as "another admin" unless
    they *are* the target) - so the self-delete guard alone already makes
    "zero admins" unreachable through the normal actor/target combinations.
    This guard stays anyway, the same way this codebase layers multiple
    independent checks around every other irreversible operation (the
    maintenance reset needs a capability *and* an environment flag *and* a
    typed phrase, each already sufficient alone). Reaching it here requires
    bypassing the scope check that would otherwise always intercept first.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    world["other_admin"].delete()  # world["admin"] is now the only admin
    monkeypatch.setattr("apps.accounts.services.can_delete_user", lambda actor, target: True)

    with pytest.raises(ValidationError, match="utolsó adminisztrátor"):
        delete_user_permanently(
            target=world["admin"],
            actor=world["management"],
            confirmation_username=world["admin"].username,
        )
    assert User.objects.filter(pk=world["admin"].pk).exists()


def test_an_admin_can_be_deleted_while_another_remains(world):
    delete_user_permanently(
        target=world["other_admin"],
        actor=world["admin"],
        confirmation_username=world["other_admin"].username,
    )
    from django.contrib.auth import get_user_model

    assert not get_user_model().objects.filter(pk=world["other_admin"].pk).exists()


def test_deletion_is_audited_with_a_snapshot_since_the_row_is_gone(world):
    target = world["porter"]
    target_pk = target.pk
    delete_user_permanently(
        target=target, actor=world["admin"], confirmation_username=target.username
    )

    entry = AuditLog.objects.get(action=AuditAction.DELETE, target_id=str(target_pk))
    assert entry.old_value["username"] == target.username
    assert entry.username == world["admin"].username


# --------------------------------------------------------------------------
# History survives: SET_NULL, not cascade, everywhere except Teacher
# --------------------------------------------------------------------------


def test_presence_events_survive_deletion_with_attribution_cleared(world):
    f.seed_status_types()
    from apps.presence.models import PresenceEvent
    from apps.presence.services import change_student_presence

    admin, other_admin, student = world["admin"], world["other_admin"], world["student"]
    event = change_student_presence(
        student=student, new_status="outside", actor=other_admin, enforce_permissions=False
    )
    assert event.created_by_id == other_admin.pk

    delete_user_permanently(
        target=other_admin, actor=admin, confirmation_username=other_admin.username
    )

    event.refresh_from_db()
    assert PresenceEvent.objects.filter(pk=event.pk).exists()  # the row survives
    assert event.created_by_id is None  # attribution cleared, not the row


def test_room_check_created_by_is_nulled_not_cascaded(world):
    """The teacher who rated a room can be deleted; the rating stays."""
    from apps.inspections.services import open_room_check_session, save_room_check

    room = f.make_room()
    teacher_user = world["teacher_profile"].user
    teacher_user.extra_capabilities = [Capability.EDIT_ROOM_CHECKS]
    teacher_user.save()
    teacher_user.refresh_capabilities()

    import datetime as dt

    session = open_room_check_session(
        date=dt.date(2026, 9, 10), floor=room.floor, actor=world["admin"]
    )
    check = session.checks.get(room=room)
    # save_room_check re-fetches and returns its own instance; asserting
    # against the stale local `check` here would check the object as it was
    # before the save.
    check = save_room_check(room_check=check, actor=teacher_user, rating=4)
    assert check.checked_by_id == teacher_user.pk

    delete_user_permanently(
        target=teacher_user, actor=world["admin"], confirmation_username=teacher_user.username
    )

    check.refresh_from_db()
    assert check.rating == 4  # the rating survives
    assert check.checked_by_id is None  # attribution cleared, not the row


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------


def test_teacher_gets_403_on_the_delete_page(client, world):
    client.force_login(world["management"])
    response = client.get(
        reverse("accounts:user_delete", kwargs={"user_id": world["porter"].pk})
    )
    assert response.status_code == 403


def test_get_shows_confirmation_without_deleting(client, world):
    client.force_login(world["admin"])
    response = client.get(
        reverse("accounts:user_delete", kwargs={"user_id": world["porter"].pk})
    )
    assert response.status_code == 200
    from django.contrib.auth import get_user_model

    assert get_user_model().objects.filter(pk=world["porter"].pk).exists()


def test_posting_the_right_username_deletes_the_account(client, world):
    client.force_login(world["admin"])
    target = world["porter"]
    response = client.post(
        reverse("accounts:user_delete", kwargs={"user_id": target.pk}),
        {"confirmation_username": target.username},
        follow=True,
    )
    assert response.status_code == 200
    from django.contrib.auth import get_user_model

    assert not get_user_model().objects.filter(pk=target.pk).exists()


def test_posting_the_wrong_username_does_not_delete(client, world):
    client.force_login(world["admin"])
    target = world["porter"]
    client.post(
        reverse("accounts:user_delete", kwargs={"user_id": target.pk}),
        {"confirmation_username": "wrong"},
    )
    from django.contrib.auth import get_user_model

    assert get_user_model().objects.filter(pk=target.pk).exists()


def test_the_delete_link_only_shows_up_where_it_is_allowed(client, world):
    client.force_login(world["admin"])

    porter_page = client.get(
        reverse("accounts:user_edit", kwargs={"user_id": world["porter"].pk})
    ).content.decode()
    assert "Fiók végleges törlése" in porter_page

    student_page = client.get(
        reverse("accounts:user_edit", kwargs={"user_id": world["student"].user.pk})
    ).content.decode()
    assert "Fiók végleges törlése" not in student_page


def test_management_never_sees_the_delete_link(client, world):
    client.force_login(world["management"])
    body = client.get(
        reverse("accounts:user_edit", kwargs={"user_id": world["porter"].pk})
    ).content.decode()
    assert "Fiók végleges törlése" not in body

"""Object-level authorization (spec 57, 58, 62).

These are the tests the spec calls out explicitly: capability checks alone are
never enough, so each case goes through the scoped querysets and services.
"""

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.accounts.capabilities import Capability, Role
from apps.core.models import ModuleKey, SystemModule
from apps.students.selectors import (
    can_edit_student,
    editable_fields_for,
    student_queryset_for_user,
)
from apps.students.services import archive_student, update_student
from apps.weekend.services import review_weekend_stay, submit_weekend_stay

from . import factories as f

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    f.seed_status_types()
    f.seed_modules()
    year = f.make_school_year()
    group_a, group_b = f.make_group("A1"), f.make_group("B1")
    return {
        "year": year,
        "group_a": group_a,
        "group_b": group_b,
        "teacher_a": f.make_teacher(group=group_a),
        "student_a": f.make_student(group=group_a, name="A Diák"),
        "student_b": f.make_student(group=group_b, name="B Diák"),
        "admin": f.make_user(Role.ADMIN),
        "management": f.make_user(Role.MANAGEMENT),
        "porter": f.make_user(Role.PORTER),
    }


def test_student_sees_only_themselves(world):
    visible = student_queryset_for_user(world["student_a"].user)
    assert list(visible) == [world["student_a"]]


def test_teacher_sees_only_their_groups(world):
    visible = student_queryset_for_user(world["teacher_a"].user)
    assert world["student_a"] in visible
    assert world["student_b"] not in visible


def test_teacher_with_all_access_sees_everyone(world):
    teacher = world["teacher_a"]
    teacher.has_all_student_access = True
    teacher.save()

    visible = student_queryset_for_user(teacher.user)
    assert world["student_b"] in visible


def test_management_sees_everyone(world):
    visible = student_queryset_for_user(world["management"])
    assert {world["student_a"], world["student_b"]} <= set(visible)


def test_teacher_cannot_edit_a_student_outside_their_groups(world):
    with pytest.raises(PermissionDenied):
        update_student(
            student=world["student_b"],
            changes={"phone": "+36301234567"},
            actor=world["teacher_a"].user,
        )


def test_teacher_field_whitelist_is_enforced(world):
    teacher_user = world["teacher_a"].user
    student = world["student_a"]

    assert can_edit_student(teacher_user, student)
    assert editable_fields_for(teacher_user, student) == frozenset(
        student.TEACHER_EDITABLE_FIELDS
    )

    update_student(student=student, changes={"phone": "+36301234567"}, actor=teacher_user)
    student.refresh_from_db()
    assert student.phone == "+36301234567"

    with pytest.raises(PermissionDenied):
        update_student(
            student=student, changes={"medical_notes": "titkos"}, actor=teacher_user
        )


def test_management_cannot_delete_all_data(world):
    assert not world["management"].has_capability(Capability.DELETE_ALL_DATA)
    assert world["admin"].has_capability(Capability.DELETE_ALL_DATA)


def test_teacher_cannot_archive_students(world):
    with pytest.raises(PermissionDenied):
        archive_student(student=world["student_a"], actor=world["teacher_a"].user)


def test_porter_can_view_but_not_edit(world):
    porter = world["porter"]
    assert porter.has_capability(Capability.VIEW_PRESENCE)
    assert not porter.has_capability(Capability.EDIT_PRESENCE)
    assert not porter.has_capability(Capability.EDIT_EVENING_CHECK)


def test_presence_is_wider_than_record_access_for_a_teacher(world):
    """The two scopes are deliberately different: a duty teacher sees every
    student's presence, but only their own group's records."""
    from apps.students.selectors import presence_queryset_for_user

    teacher_user = world["teacher_a"].user
    records = set(student_queryset_for_user(teacher_user))
    presence = set(presence_queryset_for_user(teacher_user))

    assert world["student_b"] not in records
    assert world["student_b"] in presence


def test_student_cannot_approve_their_own_weekend_request(world):
    student = world["student_a"]
    stay = submit_weekend_stay(
        student=student, actor=student.user, friday_stay=True
    )
    with pytest.raises(PermissionDenied):
        review_weekend_stay(stay=stay, actor=student.user, approve=True)


def test_revoked_capability_wins_over_the_role(world):
    teacher_user = world["teacher_a"].user
    assert teacher_user.has_capability(Capability.EDIT_PRESENCE)

    teacher_user.revoked_capabilities = [Capability.EDIT_PRESENCE]
    teacher_user.save()
    teacher_user.refresh_capabilities()

    assert not teacher_user.has_capability(Capability.EDIT_PRESENCE)


# --------------------------------------------------------------------------
# View-level checks
# --------------------------------------------------------------------------


def test_student_cannot_open_another_students_page(client, world):
    """A student lacks VIEW_STUDENTS entirely, so the capability gate stops them."""
    client.force_login(world["student_a"].user)
    response = client.get(
        reverse("students:detail", kwargs={"student_id": world["student_b"].pk})
    )
    assert response.status_code == 403


def test_teacher_cannot_open_a_student_outside_their_groups(client, world):
    """The capability passes here; only the scoped queryset stops the request."""
    client.force_login(world["teacher_a"].user)

    allowed = client.get(
        reverse("students:detail", kwargs={"student_id": world["student_a"].pk})
    )
    denied = client.get(
        reverse("students:detail", kwargs={"student_id": world["student_b"].pk})
    )

    assert allowed.status_code == 200
    assert denied.status_code == 404  # outside the scope, so it does not exist


def test_porter_cannot_post_a_presence_change(client, world):
    from apps.presence.services import ensure_presence_row

    presence = ensure_presence_row(world["student_a"])
    client.force_login(world["porter"])

    response = client.post(
        reverse("presence:set_status", kwargs={"student_id": world["student_a"].pk}),
        {"status": "outside"},
        follow=True,
    )

    presence.refresh_from_db()
    assert response.status_code == 200
    assert presence.status.code == "inside"  # unchanged


def test_disabled_module_blocks_its_views(client, world):
    SystemModule.objects.filter(key=ModuleKey.WEEKEND_STAY).update(is_enabled=False)
    client.force_login(world["management"])

    response = client.get(reverse("weekend:index"))
    assert response.status_code == 403


def test_disabled_module_disappears_from_navigation(client, world):
    client.force_login(world["management"])
    with_module = client.get(reverse("portal:dashboard")).content.decode()
    assert "Hétvégi bennmaradás" in with_module

    SystemModule.objects.filter(key=ModuleKey.WEEKEND_STAY).update(is_enabled=False)
    without_module = client.get(reverse("portal:dashboard")).content.decode()
    assert "Hétvégi bennmaradás" not in without_module


def test_anonymous_is_redirected_to_login(client):
    response = client.get(reverse("presence:current"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]

"""Leave permissions, history and automatic expiry (spec 38-40)."""

import datetime as dt

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.accounts.capabilities import Role
from apps.leave_permissions.models import (
    HistoryAction,
    LeavePermission,
    LeavePermissionHistory,
    PermissionStatus,
)
from apps.leave_permissions.services import (
    expire_leave_permissions,
    grant_leave_permission,
    revoke_leave_permission,
)
from apps.leave_permissions.tasks import expire_leave_permissions_task

from . import factories as f

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    f.seed_status_types()
    f.seed_modules()
    f.make_school_year()
    group = f.make_group()
    return {
        "student": f.make_student(group=group),
        "group": group,
        "management": f.make_user(Role.MANAGEMENT),
        "teacher": f.make_teacher(group=group),
        "porter": f.make_user(Role.PORTER),
    }


def test_grant_creates_current_state_and_history(setup):
    permission = grant_leave_permission(
        student=setup["student"],
        actor=setup["management"],
        value="Hétköznap 20:00-ig",
        expires_at=timezone.now() + dt.timedelta(days=7),
    )

    assert permission.status == PermissionStatus.ACTIVE
    assert permission.is_active

    history = LeavePermissionHistory.objects.get(student=setup["student"])
    assert history.action == HistoryAction.GRANT
    assert history.new_status == PermissionStatus.ACTIVE


def test_updating_appends_history_without_destroying_it(setup):
    grant_leave_permission(student=setup["student"], actor=setup["management"], value="A")
    grant_leave_permission(student=setup["student"], actor=setup["management"], value="B")

    entries = LeavePermissionHistory.objects.filter(student=setup["student"])
    assert entries.count() == 2
    assert entries.first().action == HistoryAction.UPDATE
    assert entries.first().previous_value == "A"


def test_revoke_transitions_and_records(setup):
    grant_leave_permission(student=setup["student"], actor=setup["management"], value="A")
    permission = revoke_leave_permission(
        student=setup["student"], actor=setup["management"], note="szabályszegés"
    )

    assert permission.status == PermissionStatus.REVOKED
    assert not permission.is_active
    assert LeavePermissionHistory.objects.filter(action=HistoryAction.REVOKE).exists()


def test_revoking_without_an_active_permission_fails(setup):
    with pytest.raises(ValidationError):
        revoke_leave_permission(student=setup["student"], actor=setup["management"])


def test_expiry_must_be_in_the_future(setup):
    with pytest.raises(ValidationError):
        grant_leave_permission(
            student=setup["student"],
            actor=setup["management"],
            expires_at=timezone.now() - dt.timedelta(hours=1),
        )


def test_porter_cannot_grant(setup):
    with pytest.raises(PermissionDenied):
        grant_leave_permission(student=setup["student"], actor=setup["porter"])


def test_teacher_cannot_grant_outside_their_scope(setup):
    outsider = f.make_student(group=f.make_group())
    with pytest.raises(PermissionDenied):
        grant_leave_permission(student=outsider, actor=setup["teacher"].user)


def test_expiry_job_expires_only_due_permissions(setup):
    due = setup["student"]
    not_due = f.make_student(group=setup["group"])

    grant_leave_permission(
        student=due,
        actor=setup["management"],
        expires_at=timezone.now() + dt.timedelta(minutes=1),
    )
    grant_leave_permission(
        student=not_due,
        actor=setup["management"],
        expires_at=timezone.now() + dt.timedelta(days=30),
    )
    # Move the first one's expiry into the past without going through the service.
    LeavePermission.objects.filter(student=due).update(
        expires_at=timezone.now() - dt.timedelta(minutes=1)
    )

    assert expire_leave_permissions() == 1
    assert LeavePermission.objects.get(student=due).status == PermissionStatus.EXPIRED
    assert LeavePermission.objects.get(student=not_due).status == PermissionStatus.ACTIVE


def test_expiry_job_is_idempotent(setup):
    grant_leave_permission(
        student=setup["student"],
        actor=setup["management"],
        expires_at=timezone.now() + dt.timedelta(minutes=1),
    )
    LeavePermission.objects.update(expires_at=timezone.now() - dt.timedelta(minutes=1))

    assert expire_leave_permissions_task() == 1
    assert expire_leave_permissions_task() == 0
    assert (
        LeavePermissionHistory.objects.filter(action=HistoryAction.EXPIRE).count() == 1
    )


def test_permissions_without_an_expiry_never_expire(setup):
    grant_leave_permission(student=setup["student"], actor=setup["management"], value="állandó")
    assert expire_leave_permissions() == 0
    assert LeavePermission.objects.get().status == PermissionStatus.ACTIVE

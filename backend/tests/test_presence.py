"""Presence transitions: current state vs immutable history (spec 12-14, 62)."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.capabilities import Role
from apps.presence.models import PresenceEvent, PresenceSource, StatusType, StudentPresence
from apps.presence.services import (
    change_student_presence,
    ensure_presence_row,
    student_leave,
    student_return,
)

from . import factories as f

pytestmark = pytest.mark.django_db


@pytest.fixture
def statuses():
    return f.seed_status_types()


@pytest.fixture
def student(statuses):
    return f.make_student()


def test_change_records_current_state_and_history(student):
    ensure_presence_row(student)
    actor = f.make_user(Role.ADMIN)

    event = change_student_presence(
        student=student, new_status="outside", actor=actor, reason="fogorvos"
    )

    presence = StudentPresence.objects.get(student=student)
    assert presence.status.code == "outside"
    assert presence.note == "fogorvos"
    assert event.old_status.code == "inside"
    assert event.new_status.code == "outside"
    assert event.source == PresenceSource.ADMIN


def test_history_is_append_only(student):
    actor = f.make_user(Role.ADMIN)
    change_student_presence(student=student, new_status="outside", actor=actor)
    change_student_presence(student=student, new_status="inside", actor=actor)

    assert PresenceEvent.objects.filter(student=student).count() == 2

    event = PresenceEvent.objects.filter(student=student).first()
    with pytest.raises(ValueError):
        event.save()


def test_repeated_identical_status_is_not_recorded(student):
    actor = f.make_user(Role.ADMIN)
    change_student_presence(student=student, new_status="outside", actor=actor)
    result = change_student_presence(student=student, new_status="outside", actor=actor)

    assert result is None
    assert PresenceEvent.objects.filter(student=student).count() == 1


def test_status_requiring_note_is_rejected_without_one(student):
    actor = f.make_user(Role.ADMIN)
    with pytest.raises(ValidationError):
        change_student_presence(student=student, new_status="other", actor=actor)


def test_inactive_status_cannot_be_selected(student):
    StatusType.objects.filter(code="doctor").update(is_active=False)
    actor = f.make_user(Role.ADMIN)
    with pytest.raises(ValidationError):
        change_student_presence(student=student, new_status="doctor", actor=actor)


def test_student_can_change_only_their_own_presence(statuses):
    student_a = f.make_student()
    student_b = f.make_student()

    student_return(student=student_a, actor=student_a.user)

    with pytest.raises(PermissionDenied):
        student_return(student=student_b, actor=student_a.user)


def test_student_leave_requires_a_leaving_status(student):
    with pytest.raises(ValidationError):
        student_leave(student=student, actor=student.user, status_code="inside")

    event = student_leave(student=student, actor=student.user, status_code="home")
    assert event.new_status.code == "home"
    assert event.source == PresenceSource.STUDENT


def test_porter_cannot_modify_presence(student):
    porter = f.make_user(Role.PORTER)
    with pytest.raises(PermissionDenied):
        change_student_presence(student=student, new_status="outside", actor=porter)


def test_teacher_cannot_modify_presence_outside_their_groups(statuses):
    group_a = f.make_group()
    group_b = f.make_group()
    teacher = f.make_teacher(group=group_a)
    outsider = f.make_student(group=group_b)

    with pytest.raises(PermissionDenied):
        change_student_presence(student=outsider, new_status="outside", actor=teacher.user)

    own = f.make_student(group=group_a)
    assert change_student_presence(
        student=own, new_status="outside", actor=teacher.user
    ) is not None

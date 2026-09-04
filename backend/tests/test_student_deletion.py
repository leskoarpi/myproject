"""Permanent student deletion.

Deliberately wider than ``apps.accounts.services.delete_user_permanently``:
that one refuses students outright (spec section 7 says archive instead),
but here the operator has explicitly asked for the stronger tool - so this
one takes the student's whole history down with it, not just the login.
Admin-only (``Capability.DELETE_STUDENTS``), confirmed by typed username.
"""

import datetime as dt

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.accounts.capabilities import Capability, Role
from apps.audit.models import AuditAction, AuditLog
from apps.dormcalendar.services import get_or_create_day
from apps.inspections.models import EveningCheckResult, RoomCheckStudentResult
from apps.inspections.services import (
    open_evening_session,
    open_room_check_session,
    save_evening_result,
    save_student_morning_status,
)
from apps.leave_permissions.models import PassEligibility, PassRuleHistory
from apps.leave_permissions.services import set_pass_rule
from apps.presence.models import PresenceEvent, StudentPresence
from apps.rooms.models import RoomAssignment
from apps.students.models import StudentChangeRequest, StudentProfile
from apps.students.selectors import can_delete_student
from apps.students.services import delete_student_permanently, submit_student_change_request
from apps.weekend.models import WeekendStay, friday_of

from . import factories as f

pytestmark = pytest.mark.django_db

A_THURSDAY = dt.date(2026, 9, 10)  # evening_check_required=True, unlike Fri/Sat


@pytest.fixture
def world():
    f.seed_status_types()
    f.seed_modules()
    year = f.make_school_year()
    room = f.make_room(number="101", floor=1, capacity=4)
    student = f.make_student(name="Törlendő Diák")
    f.assign(student, room, school_year=year)
    return {
        "admin": f.make_user(Role.ADMIN, is_superuser=True, is_staff=True),
        "management": f.make_user(Role.MANAGEMENT),
        "student": student,
        "room": room,
        "year": year,
    }


def _live_in_history(world):
    """Create one row in every PROTECT-guarded table that references the
    student, plus the CASCADE ones, so a delete has real history to clear."""
    student, admin, room = world["student"], world["admin"], world["room"]

    day = get_or_create_day(A_THURSDAY, world["year"])
    evening_session = open_evening_session(calendar_day=day, floor=room.floor, actor=admin)
    save_evening_result(session=evening_session, student=student, status="inside", actor=admin)

    room_check_session = open_room_check_session(date=A_THURSDAY, floor=room.floor, actor=admin)
    room_check = room_check_session.checks.get(room=room)
    save_student_morning_status(
        room_check=room_check, student=student, actor=admin, status="inside"
    )

    set_pass_rule(
        student=student, actor=admin, eligibility=PassEligibility.TEACHER_ONLY, note="teszt"
    )

    submit_student_change_request(
        student=student, actor=admin, proposed_changes={"school_class": "11.B"}
    )

    from apps.weekend.services import submit_weekend_stay

    submit_weekend_stay(
        student=student,
        actor=admin,
        weekend_start=friday_of(A_THURSDAY),
        friday_stay=True,
        note="teszt",
    )


# --------------------------------------------------------------------------
# Capability
# --------------------------------------------------------------------------


def test_only_admin_holds_delete_students(world):
    assert world["admin"].has_capability(Capability.DELETE_STUDENTS)
    assert not world["management"].has_capability(Capability.DELETE_STUDENTS)


def test_delete_students_cannot_be_granted_as_a_one_off(world):
    from apps.accounts.services import set_user_capabilities

    set_user_capabilities(
        target=world["management"],
        actor=world["admin"],
        extra={Capability.DELETE_STUDENTS},
        revoked=set(),
    )
    world["management"].refresh_from_db()
    world["management"].refresh_capabilities()
    assert not world["management"].has_capability(Capability.DELETE_STUDENTS)


# --------------------------------------------------------------------------
# Selector
# --------------------------------------------------------------------------


def test_admin_can_delete_a_student(world):
    assert can_delete_student(world["admin"], world["student"])


def test_management_cannot_delete_a_student(world):
    assert not can_delete_student(world["management"], world["student"])


# --------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------


def test_wrong_confirmation_username_is_rejected(world):
    student = world["student"]
    with pytest.raises(ValidationError):
        delete_student_permanently(
            student=student, actor=world["admin"], confirmation_username="not-the-username"
        )
    student.refresh_from_db()  # still exists


def test_management_cannot_delete_through_the_service(world):
    with pytest.raises(PermissionDenied):
        delete_student_permanently(
            student=world["student"],
            actor=world["management"],
            confirmation_username=world["student"].user.username,
        )


def test_deleting_a_student_removes_the_account_and_all_of_its_history(world):
    _live_in_history(world)
    student = world["student"]
    username = student.user.username
    student_pk, user_pk = student.pk, student.user.pk

    assert EveningCheckResult.objects.filter(student_id=student_pk).exists()
    assert RoomCheckStudentResult.objects.filter(student_id=student_pk).exists()
    assert RoomAssignment.objects.filter(student_id=student_pk).exists()
    assert PassRuleHistory.objects.filter(student_id=student_pk).exists()
    assert StudentChangeRequest.objects.filter(student_id=student_pk).exists()
    assert WeekendStay.objects.filter(student_id=student_pk).exists()
    assert StudentPresence.objects.filter(student_id=student_pk).exists()

    snapshot = delete_student_permanently(
        student=student, actor=world["admin"], confirmation_username=username
    )
    assert snapshot["full_name"] == "Törlendő Diák"

    from django.contrib.auth import get_user_model

    User = get_user_model()
    assert not StudentProfile.objects.filter(pk=student_pk).exists()
    assert not User.objects.filter(pk=user_pk).exists()

    # PROTECT-guarded history: explicitly cleared.
    assert not EveningCheckResult.objects.filter(student_id=student_pk).exists()
    assert not RoomCheckStudentResult.objects.filter(student_id=student_pk).exists()
    assert not RoomAssignment.objects.filter(student_id=student_pk).exists()
    assert not PassRuleHistory.objects.filter(student_id=student_pk).exists()

    # CASCADE relations: gone for free with the profile.
    assert not StudentChangeRequest.objects.filter(student_id=student_pk).exists()
    assert not WeekendStay.objects.filter(student_id=student_pk).exists()
    assert not StudentPresence.objects.filter(student_id=student_pk).exists()


def test_deletion_is_audited_with_a_snapshot_since_the_row_is_gone(world):
    student = world["student"]
    username = student.user.username
    student_pk = student.pk

    delete_student_permanently(
        student=student, actor=world["admin"], confirmation_username=username
    )

    entry = AuditLog.objects.get(action=AuditAction.DELETE, target_id=str(student_pk))
    assert entry.old_value["username"] == username
    assert entry.username == world["admin"].username


# --------------------------------------------------------------------------
# Presence events specifically: PROTECT, and easy to miss since they also
# get written by the ordinary "current status" flow, not just the round.
# --------------------------------------------------------------------------


def test_presence_event_history_is_cleared_too(world):
    from apps.presence.services import change_student_presence

    student = world["student"]
    change_student_presence(
        student=student, new_status="outside", actor=world["admin"], enforce_permissions=False
    )
    assert PresenceEvent.objects.filter(student=student).exists()

    delete_student_permanently(
        student=student, actor=world["admin"], confirmation_username=student.user.username
    )
    assert not PresenceEvent.objects.filter(student_id=student.pk).exists()


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------


def test_management_gets_403_on_the_delete_page(client, world):
    client.force_login(world["management"])
    response = client.get(
        reverse("students:delete", kwargs={"student_id": world["student"].pk})
    )
    assert response.status_code == 403


def test_get_shows_confirmation_without_deleting(client, world):
    client.force_login(world["admin"])
    response = client.get(
        reverse("students:delete", kwargs={"student_id": world["student"].pk})
    )
    assert response.status_code == 200
    assert StudentProfile.objects.filter(pk=world["student"].pk).exists()


def test_posting_the_right_username_deletes_the_student(client, world):
    client.force_login(world["admin"])
    student = world["student"]
    response = client.post(
        reverse("students:delete", kwargs={"student_id": student.pk}),
        {"confirmation_username": student.user.username},
        follow=True,
    )
    assert response.status_code == 200
    assert not StudentProfile.objects.filter(pk=student.pk).exists()


def test_posting_the_wrong_username_does_not_delete(client, world):
    client.force_login(world["admin"])
    student = world["student"]
    client.post(
        reverse("students:delete", kwargs={"student_id": student.pk}),
        {"confirmation_username": "wrong"},
    )
    assert StudentProfile.objects.filter(pk=student.pk).exists()


def test_the_delete_link_only_shows_up_for_admin(client, world):
    student = world["student"]

    client.force_login(world["admin"])
    admin_page = client.get(
        reverse("students:detail", kwargs={"student_id": student.pk})
    ).content.decode()
    assert "végleges törlése" in admin_page

    client.force_login(world["management"])
    management_page = client.get(
        reverse("students:detail", kwargs={"student_id": student.pk})
    ).content.decode()
    assert "végleges törlése" not in management_page

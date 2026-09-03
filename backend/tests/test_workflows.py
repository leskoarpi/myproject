"""End-to-end workflows through the HTTP layer (spec 62 integration tests)."""

import datetime as dt

import pytest
from django.urls import reverse

from apps.accounts.capabilities import Role
from apps.audit.models import AuditAction, AuditLog
from apps.core.models import SchoolYear, SystemModule
from apps.dormcalendar.services import get_or_create_day
from apps.inspections.models import EveningCheckSession, InspectionState
from apps.presence.models import PresenceEvent
from apps.students.models import ChangeRequestStatus, StudentChangeRequest
from apps.students.services import (
    approve_student_change_request,
    reject_student_change_request,
    submit_student_change_request,
)

from . import factories as f

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    f.seed_status_types()
    f.seed_modules()
    year = f.make_school_year()
    group = f.make_group()
    room = f.make_room(floor=1, capacity=4)
    student = f.make_student(group=group, name="Teszt Diák")
    f.assign(student, room, school_year=year)
    return {
        "year": year,
        "group": group,
        "room": room,
        "student": student,
        "teacher": f.make_teacher(group=group),
        "management": f.make_user(Role.MANAGEMENT),
        "admin": f.make_user(Role.ADMIN),
    }


def test_student_self_service_presence_round_trip(client, world):
    student = world["student"]
    client.force_login(student.user)

    dashboard = client.get(reverse("portal:dashboard"))
    assert dashboard.status_code == 200
    assert "KIMENTEM" in dashboard.content.decode()

    client.post(reverse("presence:self_leave"), {"status": "home", "reason": "hétvége"})
    student.refresh_from_db()
    assert student.presence.status.code == "home"

    client.post(reverse("presence:self_return"))
    student.refresh_from_db()
    assert student.presence.status.code == "inside"
    assert PresenceEvent.objects.filter(student=student).count() == 2


def test_evening_inspection_through_the_web(client, world):
    admin = world["admin"]
    client.force_login(admin)
    day = get_or_create_day(dt.date(2025, 11, 11))

    floor_url = reverse(
        "inspections:evening_floor", kwargs={"date": "2025-11-11", "floor": 1}
    )
    assert client.get(floor_url).status_code == 200

    session = EveningCheckSession.objects.get(calendar_day=day, floor=1)
    client.post(
        reverse(
            "inspections:evening_save_result",
            kwargs={"session_id": session.pk, "student_id": world["student"].pk},
        ),
        {"status": "inside"},
    )
    assert session.results.count() == 1

    client.post(
        reverse("inspections:evening_close", kwargs={"session_id": session.pk})
    )
    session.refresh_from_db()
    assert session.state == InspectionState.CLOSED


def test_change_request_workflow(world):
    teacher_user = world["teacher"].user
    student = world["student"]

    request_obj = submit_student_change_request(
        student=student,
        actor=teacher_user,
        proposed_changes={"guardian_name": "Új Gondviselő"},
        reason="szülő jelezte",
    )
    student.refresh_from_db()
    assert request_obj.status == ChangeRequestStatus.PENDING
    assert student.guardian_name == ""  # nothing applied yet

    approve_student_change_request(
        change_request=request_obj, actor=world["management"], note="rendben"
    )
    student.refresh_from_db()
    request_obj.refresh_from_db()
    assert student.guardian_name == "Új Gondviselő"
    assert request_obj.status == ChangeRequestStatus.APPROVED


def test_rejected_change_request_leaves_the_student_untouched(world):
    request_obj = submit_student_change_request(
        student=world["student"],
        actor=world["teacher"].user,
        proposed_changes={"school_class": "11.B"},
    )
    reject_student_change_request(
        change_request=request_obj, actor=world["management"], note="nem indokolt"
    )
    world["student"].refresh_from_db()
    assert world["student"].school_class == "10.A"
    assert StudentChangeRequest.objects.get().status == ChangeRequestStatus.REJECTED


def test_a_reviewed_request_cannot_be_reviewed_twice(world):
    from django.core.exceptions import ValidationError

    request_obj = submit_student_change_request(
        student=world["student"],
        actor=world["teacher"].user,
        proposed_changes={"school_class": "11.B"},
    )
    approve_student_change_request(change_request=request_obj, actor=world["management"])
    with pytest.raises(ValidationError):
        approve_student_change_request(change_request=request_obj, actor=world["management"])


def test_module_toggle_is_audited_and_leaves_data_alone(client, world):
    from apps.core.models import ModuleKey

    client.force_login(world["admin"])
    module = SystemModule.objects.get(key=ModuleKey.WEEKEND_STAY)
    student = world["student"]

    from apps.weekend.services import submit_weekend_stay

    stay = submit_weekend_stay(
        student=student, actor=student.user, friday_stay=True
    )

    client.post(reverse("core:toggle_module", kwargs={"module_id": module.pk}))
    module.refresh_from_db()
    stay.refresh_from_db()

    assert module.is_enabled is False
    assert stay.pk is not None  # historical data untouched
    assert AuditLog.objects.filter(
        action=AuditAction.UPDATE, target_type="core.SystemModule"
    ).exists()


def test_forced_password_change_blocks_the_rest_of_the_app(client, world):
    user = f.make_user(Role.MANAGEMENT, password="temporary-pass-1")
    user.must_change_password = True
    user.save()
    client.force_login(user)

    response = client.get(reverse("presence:current"))
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:password_change")

    changed = client.post(
        reverse("accounts:password_change"),
        {
            "old_password": "temporary-pass-1",
            "new_password1": "brand-new-secret-99",
            "new_password2": "brand-new-secret-99",
        },
    )
    assert changed.status_code == 302
    user.refresh_from_db()
    assert user.must_change_password is False
    assert client.get(reverse("presence:current")).status_code == 200


def test_login_throttle_kicks_in(client, settings, world):
    settings.LOGIN_RATELIMIT_ATTEMPTS = 3
    url = reverse("accounts:login")

    for _ in range(3):
        client.post(url, {"username": "someone", "password": "wrong"})

    response = client.post(url, {"username": "someone", "password": "wrong"})
    assert "Too many failed attempts" in response.content.decode()


def test_audit_log_redacts_sensitive_values(world):
    from apps.audit.services import REDACTED, record_audit

    entry = record_audit(
        user=world["admin"],
        action=AuditAction.UPDATE,
        target=world["student"],
        new_value={"medical_notes": "allergiás", "school_class": "11.A"},
    )
    assert entry.new_value["medical_notes"] == REDACTED
    assert entry.new_value["school_class"] == "11.A"


def test_only_one_school_year_can_be_active(world):
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            SchoolYear.objects.create(
                name="2099/2100",
                start_date=dt.date(2099, 9, 1),
                end_date=dt.date(2100, 6, 30),
                is_active=True,
            )


def test_reports_are_scoped_to_the_user(client, world):
    from apps.reports.queries import current_presence_report

    outsider = f.make_student(group=f.make_group(), name="Kívülálló")
    from apps.presence.services import ensure_presence_row

    ensure_presence_row(world["student"])
    ensure_presence_row(outsider)

    teacher_rows = current_presence_report(world["teacher"].user)
    names = {row["name"] for row in teacher_rows}
    assert "Teszt Diák" in names
    assert "Kívülálló" not in names


def test_maintenance_reset_is_refused_by_default(client, world, settings):
    settings.ALLOW_DESTRUCTIVE_MAINTENANCE = False
    client.force_login(world["admin"])

    response = client.post(
        reverse("core:maintenance_reset"), {"confirmation": "TOROL MINDENT"}, follow=True
    )
    assert response.status_code == 200
    assert AuditLog.objects.filter(action=AuditAction.MAINTENANCE).exists()
    assert world["student"].room_assignments.exists()

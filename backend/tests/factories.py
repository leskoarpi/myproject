"""Small hand-rolled factories. No extra dependency; just enough to make the
tests read like the domain."""

import datetime as dt

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.capabilities import Role
from apps.core.models import ModuleKey, SchoolYear, SystemModule
from apps.people.models import Group, Teacher
from apps.presence.models import StatusType
from apps.rooms.models import Room, RoomAssignment
from apps.students.models import StudentProfile

User = get_user_model()

_counter = {"n": 0}


def _next():
    _counter["n"] += 1
    return _counter["n"]


def seed_status_types():
    from apps.core.management.commands.seed_reference_data import STATUS_TYPES

    for code, label, short, color, inside, selectable, order in STATUS_TYPES:
        StatusType.objects.get_or_create(
            code=code,
            defaults={
                "label": label,
                "short_label": short,
                "color": color,
                "counts_as_inside": inside,
                "student_selectable": selectable,
                "sort_order": order,
                "is_default": code == "inside",
                "requires_note": code == "other",
            },
        )
    return StatusType.objects.all()


def seed_modules(enabled=True):
    for key in ModuleKey.values:
        SystemModule.objects.get_or_create(
            key=key, defaults={"name": key, "is_enabled": enabled}
        )


def make_school_year(active=True, start=None, end=None):
    # Wide enough to contain "today" and the fixed dates the tests use, so
    # date-range validation never trips on the clock.
    start = start or dt.date(2025, 9, 1)
    end = end or max(dt.date(2026, 6, 30), timezone.localdate() + dt.timedelta(days=365))
    if active:
        SchoolYear.objects.filter(is_active=True).update(is_active=False)
    return SchoolYear.objects.create(
        name=f"{start.year}/{end.year}-{_next()}", start_date=start, end_date=end, is_active=active
    )


def make_user(role=Role.STUDENT, username=None, **kwargs):
    n = _next()
    username = username or f"{role}{n}"
    return User.objects.create_user(
        username=username,
        email=kwargs.pop("email", f"{username}@example.invalid"),
        password=kwargs.pop("password", "test-password-123"),
        role=role,
        **kwargs,
    )


def make_group(code=None):
    n = _next()
    return Group.objects.create(code=code or f"G{n}", name=f"Csoport {n}")


def make_teacher(group=None, user=None, **kwargs):
    user = user or make_user(Role.TEACHER)
    teacher = Teacher.objects.create(
        user=user, full_name=f"Tanár {user.username}", primary_group=group, **kwargs
    )
    if group:
        teacher.groups.add(group)
    return teacher


def make_student(group=None, user=None, name=None, **kwargs):
    n = _next()
    user = user or make_user(Role.STUDENT)
    return StudentProfile.objects.create(
        user=user,
        full_name=name or f"Diák {n}",
        education_id=f"{700000000 + n}",
        group=group,
        school_class="10.A",
        **kwargs,
    )


def make_room(number=None, floor=1, capacity=4, **kwargs):
    n = _next()
    return Room.objects.create(
        number=number or f"{floor}{n:02d}", floor=floor, capacity=capacity, **kwargs
    )


def assign(student, room, school_year=None, start=None):
    school_year = school_year or SchoolYear.current() or make_school_year()
    return RoomAssignment.objects.create(
        student=student,
        room=room,
        school_year=school_year,
        start_date=start or school_year.start_date,
        is_active=True,
    )


def aware(year, month, day, hour=0, minute=0):
    return timezone.make_aware(
        dt.datetime(year, month, day, hour, minute), timezone.get_current_timezone()
    )

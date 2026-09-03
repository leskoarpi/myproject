"""Calendar generation and lookups."""

from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.accounts.capabilities import Capability
from apps.audit.services import AuditAction, record_audit
from apps.core.models import SchoolYear

from .models import CalendarDay, DayStatus


def default_status_for(day):
    return DayStatus.WEEKEND if day.weekday() >= 5 else DayStatus.NORMAL


def default_evening_check_for(day):
    # Friday and Saturday nights are covered by the weekend module instead.
    return day.weekday() not in {4, 5}


@transaction.atomic
def ensure_calendar_days(school_year=None, *, start=None, end=None):
    """Idempotently create the calendar rows for a school year.

    Safe to run repeatedly; existing days are never overwritten, so operator
    edits survive.
    """
    school_year = school_year or SchoolYear.current()
    if school_year is None:
        return 0

    start = start or school_year.start_date
    end = end or school_year.end_date
    start = max(start, school_year.start_date)
    end = min(end, school_year.end_date)
    if start > end:
        return 0

    existing = set(
        CalendarDay.objects.filter(date__gte=start, date__lte=end).values_list("date", flat=True)
    )
    to_create = []
    day = start
    while day <= end:
        if day not in existing:
            to_create.append(
                CalendarDay(
                    date=day,
                    school_year=school_year,
                    status=default_status_for(day),
                    evening_check_required=default_evening_check_for(day),
                )
            )
        day += timedelta(days=1)

    if to_create:
        CalendarDay.objects.bulk_create(to_create, ignore_conflicts=True)
    return len(to_create)


def get_or_create_day(day, school_year=None):
    existing = CalendarDay.objects.filter(date=day).first()
    if existing:
        return existing
    school_year = school_year or SchoolYear.current()
    if school_year is None:
        raise ValueError("Nincs beállítva aktív tanév.")
    obj, _ = CalendarDay.objects.get_or_create(
        date=day,
        defaults={
            "school_year": school_year,
            "status": default_status_for(day),
            "evening_check_required": default_evening_check_for(day),
        },
    )
    return obj


def today_day():
    return get_or_create_day(timezone.localdate())


@transaction.atomic
def update_calendar_day(*, day, actor, status=None, evening_check_required=None, note=None):
    if not actor.has_capability(Capability.MANAGE_CALENDAR):
        raise PermissionDenied("Nincs jogosultságod a naptár kezeléséhez.")
    before = {
        "status": day.status,
        "evening_check_required": day.evening_check_required,
        "note": day.note,
    }
    if status is not None:
        day.status = status
    if evening_check_required is not None:
        day.evening_check_required = evening_check_required
    if note is not None:
        day.note = note
    day.save(update_fields=["status", "evening_check_required", "note", "updated_at"])
    record_audit(
        user=actor,
        action=AuditAction.UPDATE,
        target=day,
        old_value=before,
        new_value={
            "status": day.status,
            "evening_check_required": day.evening_check_required,
            "note": day.note,
        },
    )
    return day

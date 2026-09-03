"""Monthly worksheet reports.

Two grids, both shaped like the paper sheets the dormitory already keeps:

* **Szobarend** - one row per room, one column per day of the month, the
  tidiness rating in the cell, with a monthly average.
* **Esti jelenlét** - one row per student, one column per day, the evening
  check result as a short code.

Both render as a printable A4-landscape table and download as a real .xlsx
worksheet. The rows are assembled here, once, and the HTML view and the Excel
writer both consume the same structure (spec section 69).
"""

import calendar
import datetime as dt
from dataclasses import dataclass, field

from django.db.models import Avg, Count, Q

from apps.inspections.models import EveningCheckResult, RoomCheck
from apps.rooms.models import Room


@dataclass
class MonthlyGrid:
    """A worksheet-shaped report: labelled rows against days of a month."""

    title: str
    year: int
    month: int
    row_headers: list  # column titles for the leading label columns
    rows: list = field(default_factory=list)
    summary_headers: list = field(default_factory=list)
    legend: list = field(default_factory=list)

    @property
    def days(self):
        return list(range(1, calendar.monthrange(self.year, self.month)[1] + 1))

    @property
    def weekend_days(self):
        return {
            day
            for day in self.days
            if dt.date(self.year, self.month, day).weekday() >= 5
        }

    @property
    def period_label(self):
        return f"{self.year}. {self.month:02d}."

    @property
    def filename_stem(self):
        return f"{self.title.lower().replace(' ', '-')}-{self.year}-{self.month:02d}"

    def cell(self, row, day):
        return row["cells"].get(day, "")


def month_bounds(year, month):
    last = calendar.monthrange(year, month)[1]
    return dt.date(year, month, 1), dt.date(year, month, last)


def room_tidiness_grid(year, month):
    """Room tidiness ratings for one month, one column per day."""
    start, end = month_bounds(year, month)

    checks = (
        RoomCheck.objects.filter(
            session__date__gte=start,
            session__date__lte=end,
            rating__isnull=False,
        )
        .select_related("room", "session")
        .order_by("room__floor", "room__number", "session__date")
    )

    by_room = {}
    for check in checks:
        entry = by_room.setdefault(check.room_id, {})
        # A room can be re-checked on the same day; the latest value wins.
        entry[check.session.date.day] = check.rating

    rows = []
    for room in Room.objects.active().order_by("floor", "number"):
        cells = by_room.get(room.pk, {})
        values = [v for v in cells.values() if v is not None]
        rows.append(
            {
                "labels": [room.number, f"{room.floor}."],
                "cells": cells,
                "summary": [
                    round(sum(values) / len(values), 2) if values else "",
                    len(values),
                ],
            }
        )

    return MonthlyGrid(
        title="Szobarend",
        year=year,
        month=month,
        row_headers=["Szoba", "Emelet"],
        rows=rows,
        summary_headers=["Átlag", "Ellenőrzés"],
        legend=[("1-5", "szobarend értékelés (5 = kifogástalan)")],
    )


def evening_presence_grid(year, month):
    """Evening check results for one month, one column per day."""
    start, end = month_bounds(year, month)

    results = (
        EveningCheckResult.objects.filter(
            session__calendar_day__date__gte=start,
            session__calendar_day__date__lte=end,
        )
        .select_related("student", "status", "session__calendar_day", "student__group")
        .order_by("student__full_name", "session__calendar_day__date")
    )

    students = {}
    by_student = {}
    for result in results:
        students.setdefault(result.student_id, result.student)
        code = result.status.short_label or result.status.label[:2]
        by_student.setdefault(result.student_id, {})[
            result.session.calendar_day.date.day
        ] = code

    rows = []
    seen_codes = {}
    for student_id, student in sorted(students.items(), key=lambda kv: kv[1].full_name):
        cells = by_student.get(student_id, {})
        inside = sum(1 for v in cells.values() if v == "B")
        rows.append(
            {
                "labels": [
                    student.current_room.number if student.current_room else "",
                    student.full_name,
                    student.group.code if student.group else "",
                ],
                "cells": cells,
                "summary": [inside, len(cells) - inside, len(cells)],
            }
        )

    from apps.presence.models import StatusType

    for status in StatusType.objects.active():
        seen_codes[status.short_label or status.label[:2]] = status.label

    return MonthlyGrid(
        title="Esti jelenlét",
        year=year,
        month=month,
        row_headers=["Szoba", "Név", "Csoport"],
        rows=rows,
        summary_headers=["Bent", "Egyéb", "Ellenőrizve"],
        legend=sorted(seen_codes.items()),
    )


GRID_BUILDERS = {
    "room_tidiness": ("Szobarend havi", room_tidiness_grid),
    "evening_presence": ("Esti jelenlét havi", evening_presence_grid),
}


def build_grid(name, year, month):
    if name not in GRID_BUILDERS:
        raise KeyError(name)
    return GRID_BUILDERS[name][1](year, month)


def room_tidiness_summary(year, month):
    """Floor-level averages, for the report header."""
    start, end = month_bounds(year, month)
    return list(
        RoomCheck.objects.filter(
            session__date__gte=start, session__date__lte=end, rating__isnull=False
        )
        .values("room__floor")
        .annotate(average=Avg("rating"), checks=Count("id"))
        .order_by("room__floor")
    )


def evening_presence_summary(year, month):
    """How many evening results were inside vs. elsewhere, per floor."""
    start, end = month_bounds(year, month)
    return list(
        EveningCheckResult.objects.filter(
            session__calendar_day__date__gte=start,
            session__calendar_day__date__lte=end,
        )
        .values("session__floor")
        .annotate(
            inside=Count("id", filter=Q(status__counts_as_inside=True)),
            total=Count("id"),
        )
        .order_by("session__floor")
    )

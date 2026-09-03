"""Monthly worksheet reports.

Two grids, both shaped like the paper sheets the dormitory already keeps, and
both **split into one section per floor** because that is how they are handed
out — a floor's teacher gets their floor's sheet:

* **Szobarend** - one row per room, one column per day, the tidiness mark in
  the cell. Room numbers and marks, nothing else.
* **Esti jelenlét** - one row per student, one column per day, ``+`` when the
  student was in at the time of the check and ``-`` when they were not. The
  room number is written once per room and united down over its residents
  rather than repeated on every row.

Both render as a printable A4-landscape page (one table per floor) and download
as an .xlsx workbook (one worksheet per floor). The rows are assembled here,
once, and the HTML view and the Excel writer both consume the same structure
(spec section 69).
"""

import calendar
import datetime as dt
from dataclasses import dataclass, field

from django.db.models import Avg, Count, Q

from apps.inspections.models import EveningCheckResult, RoomCheck
from apps.rooms.models import Room

PRESENT = "+"
ABSENT = "-"
NO_ROOM_LABEL = "Nincs szoba"


@dataclass
class GridSection:
    """One floor's worth of rows."""

    label: str
    rows: list = field(default_factory=list)

    @property
    def slug(self):
        return self.label.replace(".", "").replace(" ", "-").lower()


@dataclass
class MonthlyGrid:
    """A worksheet-shaped report: labelled rows against the days of a month."""

    title: str
    year: int
    month: int
    row_headers: list  # column titles for the leading label columns
    sections: list = field(default_factory=list)
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

    @property
    def all_rows(self):
        return [row for section in self.sections for row in section.rows]

    @property
    def is_empty(self):
        return not self.all_rows


def month_bounds(year, month):
    last = calendar.monthrange(year, month)[1]
    return dt.date(year, month, 1), dt.date(year, month, last)


def _floor_label(floor):
    return NO_ROOM_LABEL if floor is None else f"{floor}. emelet"


def _label_cells(labels, spans):
    """The label cells this row actually renders.

    ``spans`` is parallel to ``labels``: a span of 0 means the cell was united
    with the one above and is not rendered at all, which is what becomes a
    ``rowspan`` in HTML and a merged range in Excel. ``column`` is the 0-based
    label column, so a continuation row still lands in the right place.
    """
    return [
        {"text": text, "rowspan": span, "column": index}
        for index, (text, span) in enumerate(zip(labels, spans))
        if span
    ]


def room_tidiness_grid(year, month):
    """Room tidiness marks for one month, one section per floor.

    Room numbers and marks only - no floor column (the section says it) and no
    averages.
    """
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
        # A room can be re-checked on the same day; the latest value wins.
        by_room.setdefault(check.room_id, {})[check.session.date.day] = check.rating

    sections = {}
    for room in Room.objects.active().order_by("floor", "number"):
        section = sections.setdefault(room.floor, GridSection(label=_floor_label(room.floor)))
        section.rows.append(
            {
                "labels": [room.number],
                "label_cells": _label_cells([room.number], [1]),
                "cells": by_room.get(room.pk, {}),
            }
        )

    return MonthlyGrid(
        title="Szobarend",
        year=year,
        month=month,
        row_headers=["Szoba"],
        sections=[sections[floor] for floor in sorted(sections)],
        legend=[("1–5", "szobarend értékelés (5 = kifogástalan)")],
    )


def evening_presence_grid(year, month):
    """Evening presence for one month, one section per floor.

    The cell is ``+`` when the student was in at the moment of that evening's
    check and ``-`` when they were not; an empty cell means no check happened.
    Students are grouped by room and the room number is printed once per room.
    """
    start, end = month_bounds(year, month)

    results = (
        EveningCheckResult.objects.filter(
            session__calendar_day__date__gte=start,
            session__calendar_day__date__lte=end,
        )
        .select_related("student", "status", "session__calendar_day")
        .order_by("student__full_name", "session__calendar_day__date")
    )

    students = {}
    by_student = {}
    for result in results:
        students.setdefault(result.student_id, result.student)
        by_student.setdefault(result.student_id, {})[
            result.session.calendar_day.date.day
        ] = PRESENT if result.status.counts_as_inside else ABSENT

    # Group by where the student lives now: the sheet is read as a floor roster.
    # Someone who has since moved out lands in a trailing "no room" section.
    grouped = {}
    for student in students.values():
        room = student.current_room
        floor = room.floor if room else None
        grouped.setdefault(floor, {}).setdefault(
            room.number if room else "", []
        ).append(student)

    sections = []
    for floor in sorted(grouped, key=lambda f: (f is None, f)):
        section = GridSection(label=_floor_label(floor))
        for room_number in sorted(grouped[floor]):
            residents = sorted(grouped[floor][room_number], key=lambda s: s.full_name)
            for index, student in enumerate(residents):
                first = index == 0
                section.rows.append(
                    {
                        # The room number is written once and united down over
                        # its residents, so the blanks below it disappear.
                        "labels": [room_number if first else "", student.full_name],
                        "label_cells": _label_cells(
                            [room_number, student.full_name],
                            [len(residents) if first else 0, 1],
                        ),
                        "cells": by_student.get(student.pk, {}),
                        "starts_room": first,
                    }
                )
        sections.append(section)

    return MonthlyGrid(
        title="Esti jelenlét",
        year=year,
        month=month,
        row_headers=["Szoba", "Név"],
        sections=sections,
        legend=[
            (PRESENT, "bent volt az ellenőrzéskor"),
            (ABSENT, "nem volt bent"),
            ("", "nem volt ellenőrzés"),
        ],
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
    """Floor-level averages, for the reports index (not for the sheet)."""
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

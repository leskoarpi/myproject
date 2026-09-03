"""Monthly worksheet reports: room tidiness and evening presence.

Both are grids split into one section per floor — one row per room or student,
one column per day of the month — rendered as a printable page (a table per
floor) and as an .xlsx workbook (a worksheet per floor).
"""

import datetime as dt
import io

import pytest
from django.urls import reverse

from apps.accounts.capabilities import Role
from apps.dormcalendar.services import get_or_create_day
from apps.inspections.services import (
    open_evening_session,
    open_room_check_session,
    save_evening_result,
    save_room_check,
)
from apps.reports.monthly import (
    ABSENT,
    PRESENT,
    build_grid,
    evening_presence_grid,
    room_tidiness_grid,
)
from apps.reports.xlsx import grid_to_xlsx

from . import factories as f

pytestmark = pytest.mark.django_db

YEAR, MONTH = 2026, 9


@pytest.fixture
def setup():
    f.seed_status_types()
    f.seed_modules()
    year = f.make_school_year()
    rooms = {
        "101": f.make_room(number="101", floor=1, capacity=4),
        "102": f.make_room(number="102", floor=1, capacity=4),
        "201": f.make_room(number="201", floor=2, capacity=4),
    }
    students = {
        # Two share 101, so the room number must not repeat on the second.
        "anna": f.make_student(name="Anna Diák"),
        "bela": f.make_student(name="Béla Diák"),
        "cili": f.make_student(name="Cili Diák"),
    }
    f.assign(students["anna"], rooms["101"], school_year=year)
    f.assign(students["bela"], rooms["101"], school_year=year)
    f.assign(students["cili"], rooms["201"], school_year=year)
    return {"admin": f.make_user(Role.ADMIN), "rooms": rooms, "students": students}


def _rate(setup, day, room_number, rating):
    floor = setup["rooms"][room_number].floor
    session = open_room_check_session(date=day, floor=floor, actor=setup["admin"])
    check = session.checks.get(room__number=room_number)
    save_room_check(room_check=check, actor=setup["admin"], rating=rating)


def _evening(setup, day, student, status_code):
    floor = student.current_room.floor
    calendar_day = get_or_create_day(day)
    calendar_day.evening_check_required = True
    calendar_day.save()
    session = open_evening_session(calendar_day=calendar_day, floor=floor, actor=setup["admin"])
    save_evening_result(
        session=session, student=student, status=status_code, actor=setup["admin"]
    )


def _section(grid, label):
    return next(s for s in grid.sections if s.label == label)


# --------------------------------------------------------------------------
# Grid shape
# --------------------------------------------------------------------------


def test_grid_has_one_column_per_day_of_the_month(setup):
    assert room_tidiness_grid(YEAR, MONTH).days == list(range(1, 31))  # September


def test_february_length_follows_the_calendar(setup):
    assert len(room_tidiness_grid(2024, 2).days) == 29  # leap year
    assert len(room_tidiness_grid(2026, 2).days) == 28


def test_weekend_columns_are_marked(setup):
    grid = room_tidiness_grid(YEAR, MONTH)
    # 2026-09-05 is a Saturday, 2026-09-07 a Monday.
    assert 5 in grid.weekend_days
    assert 7 not in grid.weekend_days


# --------------------------------------------------------------------------
# Room tidiness: floors, room numbers and marks only
# --------------------------------------------------------------------------


def test_tidiness_is_split_by_floor(setup):
    grid = room_tidiness_grid(YEAR, MONTH)
    assert [s.label for s in grid.sections] == ["1. emelet", "2. emelet"]
    assert [r["labels"][0] for r in _section(grid, "1. emelet").rows] == ["101", "102"]
    assert [r["labels"][0] for r in _section(grid, "2. emelet").rows] == ["201"]


def test_tidiness_carries_only_room_numbers_and_marks(setup):
    grid = room_tidiness_grid(YEAR, MONTH)
    assert grid.row_headers == ["Szoba"]
    row = _section(grid, "1. emelet").rows[0]
    assert list(row["labels"]) == ["101"]  # no floor column
    assert "summary" not in row  # no average / check-count columns


def test_tidiness_places_marks_on_the_right_days(setup):
    _rate(setup, dt.date(YEAR, MONTH, 2), "101", 5)
    _rate(setup, dt.date(YEAR, MONTH, 9), "101", 3)

    row = _section(room_tidiness_grid(YEAR, MONTH), "1. emelet").rows[0]
    assert row["cells"][2] == 5
    assert row["cells"][9] == 3
    assert 3 not in row["cells"]


def test_tidiness_ignores_other_months(setup):
    _rate(setup, dt.date(YEAR, MONTH, 2), "101", 5)
    _rate(setup, dt.date(YEAR, 8, 20), "101", 1)

    row = _section(room_tidiness_grid(YEAR, MONTH), "1. emelet").rows[0]
    assert list(row["cells"]) == [2]


# --------------------------------------------------------------------------
# Evening presence: floors, +/- and no repeated room numbers
# --------------------------------------------------------------------------


def test_evening_cells_are_plus_or_minus(setup):
    student = setup["students"]["anna"]
    _evening(setup, dt.date(YEAR, MONTH, 1), student, "inside")
    _evening(setup, dt.date(YEAR, MONTH, 2), student, "home")

    row = next(
        r for r in evening_presence_grid(YEAR, MONTH).all_rows if "Anna Diák" in r["labels"]
    )
    assert row["cells"][1] == PRESENT == "+"
    assert row["cells"][2] == ABSENT == "-"
    assert row["cells"].get(3, "") == ""  # no check that day


def test_evening_has_no_summary_columns(setup):
    _evening(setup, dt.date(YEAR, MONTH, 1), setup["students"]["anna"], "inside")

    grid = evening_presence_grid(YEAR, MONTH)
    assert grid.row_headers == ["Szoba", "Név"]
    assert all("summary" not in row for row in grid.all_rows)


def test_evening_is_split_by_floor(setup):
    _evening(setup, dt.date(YEAR, MONTH, 1), setup["students"]["anna"], "inside")
    _evening(setup, dt.date(YEAR, MONTH, 1), setup["students"]["cili"], "inside")

    grid = evening_presence_grid(YEAR, MONTH)
    assert [s.label for s in grid.sections] == ["1. emelet", "2. emelet"]
    assert [r["labels"][1] for r in _section(grid, "2. emelet").rows] == ["Cili Diák"]


def test_room_number_is_written_once_per_room(setup):
    for student in (setup["students"]["anna"], setup["students"]["bela"]):
        _evening(setup, dt.date(YEAR, MONTH, 1), student, "inside")

    rows = _section(evening_presence_grid(YEAR, MONTH), "1. emelet").rows
    assert [r["labels"] for r in rows] == [
        ["101", "Anna Diák"],
        ["", "Béla Diák"],  # same room, number not repeated
    ]
    assert [r["starts_room"] for r in rows] == [True, False]


def test_the_room_cell_is_united_over_its_residents(setup):
    """The blank cells under a room number are united into it, not left empty."""
    for student in (setup["students"]["anna"], setup["students"]["bela"]):
        _evening(setup, dt.date(YEAR, MONTH, 1), student, "inside")

    rows = _section(evening_presence_grid(YEAR, MONTH), "1. emelet").rows

    first, second = rows[0]["label_cells"], rows[1]["label_cells"]
    # Row 1 renders the room spanning both residents, plus its own name cell.
    assert [(c["column"], c["text"], c["rowspan"]) for c in first] == [
        (0, "101", 2),
        (1, "Anna Diák", 1),
    ]
    # Row 2 renders no room cell at all - it is covered by the one above.
    assert [(c["column"], c["text"], c["rowspan"]) for c in second] == [
        (1, "Béla Diák", 1)
    ]


def test_a_single_resident_room_is_not_united(setup):
    _evening(setup, dt.date(YEAR, MONTH, 1), setup["students"]["cili"], "inside")

    row = _section(evening_presence_grid(YEAR, MONTH), "2. emelet").rows[0]
    assert [c["rowspan"] for c in row["label_cells"]] == [1, 1]


def test_tidiness_rows_are_never_united(setup):
    row = _section(room_tidiness_grid(YEAR, MONTH), "1. emelet").rows[0]
    assert [(c["column"], c["text"], c["rowspan"]) for c in row["label_cells"]] == [
        (0, "101", 1)
    ]


def test_a_student_without_a_room_lands_in_a_trailing_section(setup):
    student = setup["students"]["cili"]
    _evening(setup, dt.date(YEAR, MONTH, 1), student, "inside")
    student.room_assignments.update(is_active=False, end_date=dt.date(YEAR, MONTH, 2))

    grid = evening_presence_grid(YEAR, MONTH)
    assert grid.sections[-1].label == "Nincs szoba"
    assert grid.sections[-1].rows[0]["labels"] == ["", "Cili Diák"]


def test_empty_month_still_renders_a_grid(setup):
    grid = evening_presence_grid(YEAR, MONTH)
    assert grid.is_empty
    assert len(grid.days) == 30


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------


def test_xlsx_is_a_real_workbook_with_one_sheet_per_floor(setup):
    _rate(setup, dt.date(YEAR, MONTH, 2), "101", 4)
    _rate(setup, dt.date(YEAR, MONTH, 2), "201", 5)

    payload = grid_to_xlsx(room_tidiness_grid(YEAR, MONTH))
    assert payload is not None
    assert payload[:2] == b"PK"  # xlsx is a zip container

    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(payload))
    assert book.sheetnames == ["1. emelet", "2. emelet"]

    sheet = book["1. emelet"]
    assert sheet.cell(row=3, column=1).value == "Szoba"
    assert sheet.cell(row=3, column=2).value == "1"  # day columns start immediately
    assert sheet.cell(row=4, column=1).value == "101"
    assert sheet.cell(row=4, column=3).value == 4  # day 2
    assert sheet.freeze_panes == "B4"
    assert sheet.page_setup.orientation == "landscape"


def test_printable_sheet_uses_a_rowspan_for_the_room(client, setup):
    for student in (setup["students"]["anna"], setup["students"]["bela"]):
        _evening(setup, dt.date(YEAR, MONTH, 1), student, "inside")
    client.force_login(setup["admin"])

    body = client.get(
        reverse("reports:monthly", kwargs={"name": "evening_presence"}),
        {"year": YEAR, "month": MONTH},
    ).content.decode()

    assert 'rowspan="2"' in body
    assert body.count(">101<") == 1  # written once, not once per resident


def test_xlsx_merges_the_room_cell_over_its_residents(setup):
    for student in (setup["students"]["anna"], setup["students"]["bela"]):
        _evening(setup, dt.date(YEAR, MONTH, 1), student, "inside")

    from openpyxl import load_workbook

    sheet = load_workbook(io.BytesIO(grid_to_xlsx(evening_presence_grid(YEAR, MONTH))))[
        "1. emelet"
    ]
    # Rows 4 and 5 are the two residents; the room cell spans both.
    assert "A4:A5" in {str(r) for r in sheet.merged_cells.ranges}
    assert sheet.cell(row=4, column=1).value == "101"
    assert sheet.cell(row=4, column=2).value == "Anna Diák"
    assert sheet.cell(row=5, column=2).value == "Béla Diák"


def test_xlsx_evening_sheet_carries_plus_minus(setup):
    _evening(setup, dt.date(YEAR, MONTH, 1), setup["students"]["anna"], "inside")
    _evening(setup, dt.date(YEAR, MONTH, 2), setup["students"]["anna"], "home")

    from openpyxl import load_workbook

    sheet = load_workbook(io.BytesIO(grid_to_xlsx(evening_presence_grid(YEAR, MONTH))))[
        "1. emelet"
    ]
    assert sheet.cell(row=4, column=1).value == "101"
    assert sheet.cell(row=4, column=2).value == "Anna Diák"
    assert sheet.cell(row=4, column=3).value == "+"
    assert sheet.cell(row=4, column=4).value == "-"


def test_xlsx_for_an_empty_month_still_opens(setup):
    from openpyxl import load_workbook

    payload = grid_to_xlsx(evening_presence_grid(YEAR, MONTH))
    book = load_workbook(io.BytesIO(payload))
    assert book.sheetnames == ["Nincs adat"]  # a workbook needs at least one sheet


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------


def test_printable_sheet_shows_a_table_per_floor(client, setup):
    _rate(setup, dt.date(YEAR, MONTH, 2), "101", 4)
    _rate(setup, dt.date(YEAR, MONTH, 2), "201", 5)
    client.force_login(setup["admin"])

    body = client.get(
        reverse("reports:monthly", kwargs={"name": "room_tidiness"}),
        {"year": YEAR, "month": MONTH},
    ).content.decode()

    assert "1. emelet" in body
    assert "2. emelet" in body
    assert body.count('class="sheet"') == 2


def test_xlsx_download_has_the_right_content_type(client, setup):
    client.force_login(setup["admin"])
    response = client.get(
        reverse("reports:monthly_xlsx", kwargs={"name": "evening_presence"}),
        {"year": YEAR, "month": MONTH},
    )

    assert response.status_code == 200
    assert response["Content-Type"].endswith("spreadsheetml.sheet")
    assert ".xlsx" in response["Content-Disposition"]


def test_export_is_audited(client, setup):
    from apps.audit.models import AuditAction, AuditLog

    client.force_login(setup["admin"])
    client.get(
        reverse("reports:monthly_xlsx", kwargs={"name": "room_tidiness"}),
        {"year": YEAR, "month": MONTH},
    )
    assert AuditLog.objects.filter(action=AuditAction.EXPORT).exists()


def test_unknown_sheet_is_404(client, setup):
    client.force_login(setup["admin"])
    assert (
        client.get(reverse("reports:monthly", kwargs={"name": "nonsense"})).status_code == 404
    )


def test_a_teacher_cannot_reach_the_reports(client, setup):
    teacher = f.make_teacher()
    client.force_login(teacher.user)
    assert client.get(reverse("reports:index")).status_code == 403


def test_build_grid_rejects_unknown_names(setup):
    with pytest.raises(KeyError):
        build_grid("nope", YEAR, MONTH)

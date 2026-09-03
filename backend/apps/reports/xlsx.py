"""Excel export for the monthly worksheets.

Produces a real .xlsx (openpyxl), not a CSV with a spreadsheet extension, so
the sheet opens in Excel, LibreOffice or Google Sheets with the header frozen,
the weekend columns shaded and the day columns narrow enough to fit a month on
one screen.

Each floor gets its own worksheet, matching how the printed sheets are split.
A room number that covers several residents is written once and merged down
over them, the same way the printed sheet uses a rowspan.
"""

HEADER_FILL = "1F4E79"
WEEKEND_FILL = "E8EEF4"
BORDER_COLOR = "B8C4D0"
ROOM_RULE_COLOR = "6B7A8C"

HEADER_ROW = 3


def _safe_sheet_title(label, used):
    """Excel sheet names: 31 chars, no []:*?/\\, and unique within the book."""
    cleaned = "".join(ch for ch in label if ch not in set('[]:*?/\\'))[:31] or "Lap"
    title, suffix = cleaned, 2
    while title in used:
        tail = f" ({suffix})"
        title = cleaned[: 31 - len(tail)] + tail
        suffix += 1
    used.add(title)
    return title


def grid_to_xlsx(grid):
    """Render a :class:`~apps.reports.monthly.MonthlyGrid` as .xlsx bytes.

    Returns ``None`` when openpyxl is not installed, so the caller can fall
    back to the printable HTML view rather than erroring.
    """
    try:
        import io

        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError:  # pragma: no cover - optional dependency
        return None

    thin = Side(style="thin", color=BORDER_COLOR)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    room_rule = Border(
        left=thin,
        right=thin,
        top=Side(style="thin", color=ROOM_RULE_COLOR),
        bottom=thin,
    )
    header_font = Font(bold=True, color="FFFFFF", size=10)
    header_fill = PatternFill("solid", fgColor=HEADER_FILL)
    weekend_fill = PatternFill("solid", fgColor=WEEKEND_FILL)
    centered = Alignment(horizontal="center", vertical="center")
    middle_left = Alignment(horizontal="left", vertical="center")

    workbook = Workbook()
    workbook.remove(workbook.active)  # replaced by the per-floor sheets below

    label_count = len(grid.row_headers)
    total_columns = label_count + len(grid.days)
    weekend = grid.weekend_days
    used_titles = set()

    sections = grid.sections or [_empty_section(grid)]

    for section in sections:
        sheet = workbook.create_sheet(_safe_sheet_title(section.label, used_titles))

        sheet.cell(
            row=1,
            column=1,
            value=f"{grid.title} — {section.label} — {grid.period_label}",
        ).font = Font(bold=True, size=13)
        sheet.merge_cells(
            start_row=1, start_column=1, end_row=1, end_column=max(total_columns, 2)
        )

        columns = list(grid.row_headers) + [str(d) for d in grid.days]
        for index, title in enumerate(columns, start=1):
            cell = sheet.cell(row=HEADER_ROW, column=index, value=title)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = centered
            cell.border = border

        merges = []
        for offset, row in enumerate(section.rows, start=HEADER_ROW + 1):
            # A thin rule above the first resident of each room marks where the
            # next room starts.
            row_border = room_rule if row.get("starts_room") and offset > HEADER_ROW + 1 else border

            for spec in row["label_cells"]:
                column = spec["column"] + 1
                span = spec["rowspan"]
                cell = sheet.cell(row=offset, column=column, value=spec["text"])
                cell.border = row_border
                if span > 1:
                    # Give every covered cell a border before merging, or the
                    # united block loses its right-hand edge further down.
                    for covered in range(offset, offset + span):
                        sheet.cell(row=covered, column=column).border = border
                    sheet.cell(row=offset, column=column).border = row_border
                    cell.alignment = middle_left
                    merges.append((offset, column, offset + span - 1))

            column = label_count + 1
            for day in grid.days:
                cell = sheet.cell(row=offset, column=column, value=row["cells"].get(day, ""))
                cell.alignment = centered
                cell.border = row_border
                if day in weekend:
                    cell.fill = weekend_fill
                column += 1

        # Merge after writing: openpyxl clears the covered cells on merge.
        for start_row, column, end_row in merges:
            sheet.merge_cells(
                start_row=start_row, start_column=column, end_row=end_row, end_column=column
            )

        if grid.legend:
            legend_row = HEADER_ROW + len(section.rows) + 2
            sheet.cell(row=legend_row, column=1, value="Jelmagyarázat:").font = Font(bold=True)
            for index, (code, meaning) in enumerate(grid.legend, start=1):
                sheet.cell(row=legend_row + index, column=1, value=code or "(üres)")
                sheet.cell(row=legend_row + index, column=2, value=meaning)

        # Wide labels, narrow days, header and labels frozen while scrolling.
        widths = ([10, 26] + [12] * label_count)[:label_count]
        for index, width in enumerate(widths, start=1):
            sheet.column_dimensions[get_column_letter(index)].width = width
        for index in range(label_count + 1, total_columns + 1):
            sheet.column_dimensions[get_column_letter(index)].width = 4.2

        sheet.freeze_panes = sheet.cell(row=HEADER_ROW + 1, column=label_count + 1)

        # A4 landscape, header repeated on every printed page.
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.print_title_rows = f"{HEADER_ROW}:{HEADER_ROW}"

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _empty_section(grid):
    """A workbook always needs at least one sheet, even for an empty month."""
    from .monthly import GridSection

    return GridSection(label="Nincs adat")

"""PDF generation for the printable weekend roster (spec section 35).

Uses reportlab when it is installed; returns ``None`` otherwise so the caller
can fall back to the print-optimised HTML view rather than erroring.
"""

import io
import logging

logger = logging.getLogger(__name__)

RESULT_GLYPH = {"inside": "B", "outside": "K", "pending": "-", None: ""}


def _cell(check):
    if check is None:
        return ""
    return RESULT_GLYPH.get(check.result, check.result)


def weekend_roster_pdf(weekend_start, rows):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError:  # pragma: no cover - optional dependency
        logger.warning("reportlab is not installed; falling back to HTML roster")
        return None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=f"Hetvegi bennmaradas {weekend_start:%Y-%m-%d}",
    )
    styles = getSampleStyleSheet()

    header = [
        "Szoba",
        "Név",
        "Csoport",
        "P. bent",
        "P. éjszaka",
        "Szo. bent",
        "Szo. éjszaka",
        "Megjegyzés",
    ]
    data = [header]
    for row in rows:
        data.append(
            [
                row["room"],
                row["name"],
                row["group"],
                "X" if row["friday_stay"] else "",
                _cell(row["friday_night"]),
                "X" if row["saturday_stay"] else "",
                _cell(row["saturday_night"]),
                (row["note"] or "")[:60],
            ]
        )

    table = Table(
        data,
        repeatRows=1,
        colWidths=[
            20 * mm, 62 * mm, 34 * mm, 22 * mm, 26 * mm, 24 * mm, 28 * mm, 60 * mm,
        ],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#94a3b8")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
                ("ALIGN", (3, 1), (6, -1), "CENTER"),
            ]
        )
    )

    story = [
        Paragraph(
            f"Hétvégi bennmaradás - {weekend_start:%Y. %m. %d.}", styles["Heading2"]
        ),
        Spacer(1, 4 * mm),
        table,
    ]
    doc.build(story)
    return buffer.getvalue()

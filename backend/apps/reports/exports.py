"""Controlled exports (spec section 48).

Exports carry only report rows assembled by :mod:`apps.reports.queries`.
Password hashes, session data and application secrets are never part of a
report row, so they cannot leak through here.
"""

import csv
import io
import json
import zipfile

from django.http import HttpResponse

from apps.audit.services import AuditAction, record_audit

BANNED_KEYS = {"password", "session", "secret", "token", "api_key"}


def _sanitize(rows):
    return [
        {k: v for k, v in row.items() if k.lower() not in BANNED_KEYS}
        for row in rows
    ]


def rows_to_csv(rows):
    rows = _sanitize(rows)
    buffer = io.StringIO()
    if not rows:
        return ""
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def csv_response(rows, filename, *, actor=None, report_name=""):
    payload = rows_to_csv(rows)
    if actor is not None:
        record_audit(
            user=actor,
            action=AuditAction.EXPORT,
            target_type="reports",
            target_repr=report_name or filename,
            new_value={"rows": len(rows), "format": "csv"},
        )
    response = HttpResponse(payload, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def json_response(rows, filename, *, actor=None, report_name=""):
    payload = json.dumps(_sanitize(rows), ensure_ascii=False, default=str, indent=2)
    if actor is not None:
        record_audit(
            user=actor,
            action=AuditAction.EXPORT,
            target_type="reports",
            target_repr=report_name or filename,
            new_value={"rows": len(rows), "format": "json"},
        )
    response = HttpResponse(payload, content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def zip_response(named_rows, filename, *, actor=None):
    """Bundle several reports into one ZIP of CSV files."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, rows in named_rows.items():
            archive.writestr(f"{name}.csv", rows_to_csv(rows))
    if actor is not None:
        record_audit(
            user=actor,
            action=AuditAction.EXPORT,
            target_type="reports",
            target_repr=filename,
            new_value={"reports": sorted(named_rows), "format": "zip"},
        )
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

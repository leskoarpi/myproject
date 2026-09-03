import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.accounts.capabilities import Capability
from apps.accounts.permissions import require_capabilities
from apps.weekend.models import friday_of

from . import queries
from .exports import csv_response, json_response, zip_response
from .monthly import (
    GRID_BUILDERS,
    build_grid,
    evening_presence_summary,
    room_tidiness_summary,
)
from .xlsx import grid_to_xlsx


def _parse(raw, default):
    if not raw:
        return default
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        return default


def _period(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
    except ValueError:
        year, month = today.year, today.month
    if not 1 <= month <= 12:
        year, month = today.year, today.month
    return year, month


# --------------------------------------------------------------------------
# Monthly worksheets - the reports the dormitory actually prints
# --------------------------------------------------------------------------


@login_required
@require_capabilities(Capability.VIEW_REPORTS)
def index(request):
    year, month = _period(request)
    start, end = queries.default_period()
    previous_month = dt.date(year, month, 1) - dt.timedelta(days=1)
    next_month = dt.date(year, month, 1) + dt.timedelta(days=32)

    return render(
        request,
        "reports/index.html",
        {
            "year": year,
            "month": month,
            "monthly": {key: label for key, (label, _) in GRID_BUILDERS.items()},
            "prev_year": previous_month.year,
            "prev_month": previous_month.month,
            "next_year": next_month.year,
            "next_month": next_month.month,
            "tidiness_summary": room_tidiness_summary(year, month),
            "presence_summary": evening_presence_summary(year, month),
            "reports": AD_HOC_REPORTS,
            "start": _parse(request.GET.get("start"), start),
            "end": _parse(request.GET.get("end"), end),
            "can_export": request.user.has_capability(Capability.EXPORT_DATA),
        },
    )


@login_required
@require_capabilities(Capability.VIEW_REPORTS)
def monthly_sheet(request, name):
    """Printable worksheet: rows against the days of one month."""
    year, month = _period(request)
    try:
        grid = build_grid(name, year, month)
    except KeyError:
        raise Http404("Unknown report")

    previous_month = dt.date(year, month, 1) - dt.timedelta(days=1)
    next_month = dt.date(year, month, 1) + dt.timedelta(days=32)

    return render(
        request,
        "reports/monthly_sheet.html",
        {
            "grid": grid,
            "name": name,
            "prev_year": previous_month.year,
            "prev_month": previous_month.month,
            "next_year": next_month.year,
            "next_month": next_month.month,
            "can_export": request.user.has_capability(Capability.EXPORT_DATA),
        },
    )


@login_required
@require_capabilities(Capability.VIEW_REPORTS, Capability.EXPORT_DATA)
def monthly_xlsx(request, name):
    """The same worksheet as a real .xlsx file."""
    year, month = _period(request)
    try:
        grid = build_grid(name, year, month)
    except KeyError:
        raise Http404("Unknown report")

    payload = grid_to_xlsx(grid)
    if payload is None:
        messages.warning(
            request,
            "Az Excel export nem érhető el ezen a példányon; a nyomtatható nézet jelenik meg.",
        )
        return redirect(f"/reports/monthly/{name}/?year={year}&month={month}")

    from apps.audit.services import AuditAction, record_audit

    record_audit(
        user=request.user,
        action=AuditAction.EXPORT,
        target_type="reports",
        target_repr=f"{grid.title} {grid.period_label}",
        new_value={
            "rows": len(grid.all_rows),
            "sheets": [section.label for section in grid.sections],
            "format": "xlsx",
        },
    )
    response = HttpResponse(
        payload,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{grid.filename_stem}.xlsx"'
    return response


# --------------------------------------------------------------------------
# Ad-hoc reports (CSV / JSON)
# --------------------------------------------------------------------------

AD_HOC_REPORTS = {
    "presence": "Aktuális jelenlét",
    "presence_history": "Jelenléti előzmények",
    "occupancy": "Szobakihasználtság",
    "room_checks": "Szobaellenőrzések tételesen",
    "room_ratings": "Szoba átlagok",
    "weekend": "Hétvégi bennmaradás",
    "passes": "Kimenő jogosultságok",
    "pass_history": "Kimenő jogosultság előzmények",
}


def _rows_for(name, request):
    start, end = queries.default_period()
    start = _parse(request.GET.get("start"), start)
    end = _parse(request.GET.get("end"), end)
    user = request.user

    if name == "presence":
        return queries.current_presence_report(user)
    if name == "presence_history":
        return queries.presence_history_report(user, start=start, end=end)
    if name == "occupancy":
        return queries.occupancy_report()
    if name == "room_checks":
        return queries.room_check_report(start=start, end=end)
    if name == "room_ratings":
        return queries.room_rating_averages(start=start, end=end)
    if name == "weekend":
        weekend = _parse(request.GET.get("weekend"), friday_of(timezone.localdate()))
        return queries.weekend_stay_report(user, friday_of(weekend))
    if name == "passes":
        return queries.pass_rule_report(user)
    if name == "pass_history":
        return queries.pass_rule_history_report(user, start=start, end=end)
    raise Http404("Unknown report")


@login_required
@require_capabilities(Capability.VIEW_REPORTS)
def report_detail(request, name):
    if name not in AD_HOC_REPORTS:
        raise Http404("Unknown report")
    rows = _rows_for(name, request)
    start, end = queries.default_period()
    return render(
        request,
        "reports/detail.html",
        {
            "name": name,
            "title": AD_HOC_REPORTS[name],
            "rows": rows[:1000],
            "total": len(rows),
            "columns": list(rows[0].keys()) if rows else [],
            "start": _parse(request.GET.get("start"), start),
            "end": _parse(request.GET.get("end"), end),
            "can_export": request.user.has_capability(Capability.EXPORT_DATA),
        },
    )


@login_required
@require_capabilities(Capability.VIEW_REPORTS, Capability.EXPORT_DATA)
def report_export(request, name, fmt):
    if name not in AD_HOC_REPORTS:
        raise Http404("Unknown report")
    rows = _rows_for(name, request)
    if fmt == "json":
        return json_response(
            rows, f"{name}.json", actor=request.user, report_name=AD_HOC_REPORTS[name]
        )
    return csv_response(rows, f"{name}.csv", actor=request.user, report_name=AD_HOC_REPORTS[name])


@login_required
@require_capabilities(Capability.VIEW_REPORTS, Capability.EXPORT_DATA)
def export_bundle(request):
    """One ZIP with the standard operational reports."""
    bundle = {
        "jelenlet": queries.current_presence_report(request.user),
        "szobakihasznaltsag": queries.occupancy_report(),
        "kimeno_jogosultsag": queries.pass_rule_report(request.user),
    }
    stamp = timezone.localdate().isoformat()
    return zip_response(bundle, f"deakkoli-export-{stamp}.zip", actor=request.user)

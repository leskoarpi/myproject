import datetime as dt

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from apps.accounts.capabilities import Capability
from apps.accounts.permissions import require_capabilities
from apps.inspections.models import MorningSnapshot
from apps.weekend.models import friday_of

from . import queries
from .exports import csv_response, json_response, zip_response


def _parse(raw, default):
    if not raw:
        return default
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        return default


REPORTS = {
    "presence": "Aktuális jelenlét",
    "presence_history": "Jelenléti előzmények",
    "occupancy": "Szobakihasználtság",
    "morning_absences": "Reggeli hiányzások",
    "room_checks": "Szobaellenőrzések",
    "room_ratings": "Szoba átlagok",
    "weekend": "Hétvégi bennmaradás",
    "leave": "Kimenő engedélyek",
    "leave_history": "Kimenő engedély előzmények",
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
    if name == "morning_absences":
        return queries.morning_absence_summary(start=start, end=end)
    if name == "room_checks":
        return queries.room_check_report(start=start, end=end)
    if name == "room_ratings":
        return queries.room_rating_averages(start=start, end=end)
    if name == "weekend":
        weekend = _parse(request.GET.get("weekend"), friday_of(dt.date.today()))
        return queries.weekend_stay_report(friday_of(weekend))
    if name == "leave":
        return queries.leave_permission_report(user)
    if name == "leave_history":
        return queries.leave_permission_history_report(user, start=start, end=end)
    raise Http404("Unknown report")


@login_required
@require_capabilities(Capability.VIEW_REPORTS)
def index(request):
    start, end = queries.default_period()
    return render(
        request,
        "reports/index.html",
        {
            "reports": REPORTS,
            "start": _parse(request.GET.get("start"), start),
            "end": _parse(request.GET.get("end"), end),
            "can_export": request.user.has_capability(Capability.EXPORT_DATA),
        },
    )


@login_required
@require_capabilities(Capability.VIEW_REPORTS)
def report_detail(request, name):
    if name not in REPORTS:
        raise Http404("Unknown report")
    rows = _rows_for(name, request)
    start, end = queries.default_period()
    return render(
        request,
        "reports/detail.html",
        {
            "name": name,
            "title": REPORTS[name],
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
    if name not in REPORTS:
        raise Http404("Unknown report")
    rows = _rows_for(name, request)
    if fmt == "json":
        return json_response(
            rows, f"{name}.json", actor=request.user, report_name=REPORTS[name]
        )
    return csv_response(rows, f"{name}.csv", actor=request.user, report_name=REPORTS[name])


@login_required
@require_capabilities(Capability.VIEW_REPORTS, Capability.EXPORT_DATA)
def export_bundle(request):
    """One ZIP with the standard operational reports."""
    bundle = {
        "jelenlet": queries.current_presence_report(request.user),
        "szobakihasznaltsag": queries.occupancy_report(),
        "kimeno_engedelyek": queries.leave_permission_report(request.user),
    }
    stamp = dt.date.today().isoformat()
    return zip_response(bundle, f"deakkoli-export-{stamp}.zip", actor=request.user)


@login_required
@require_capabilities(Capability.VIEW_REPORTS)
def morning_report(request, snapshot_id):
    snapshot = get_object_or_404(MorningSnapshot, pk=snapshot_id)
    rows = queries.morning_report(snapshot)
    if request.GET.get("format") == "csv":
        if not request.user.has_capability(Capability.EXPORT_DATA):
            raise Http404()
        return csv_response(
            rows,
            f"reggeli-{snapshot.date:%Y-%m-%d}.csv",
            actor=request.user,
            report_name="Reggeli ellenőrzés",
        )
    return render(
        request,
        "reports/detail.html",
        {
            "name": "morning",
            "title": f"Reggeli ellenőrzés - {snapshot.date:%Y-%m-%d}",
            "rows": rows,
            "total": len(rows),
            "columns": list(rows[0].keys()) if rows else [],
            "can_export": request.user.has_capability(Capability.EXPORT_DATA),
        },
    )

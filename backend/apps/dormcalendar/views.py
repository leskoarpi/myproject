import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.capabilities import Capability
from apps.accounts.permissions import require_capabilities
from apps.core.models import SchoolYear

from .models import CalendarDay, DayStatus
from .services import ensure_calendar_days, update_calendar_day


@login_required
@require_capabilities(Capability.MANAGE_CALENDAR)
def index(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
    except ValueError:
        year, month = today.year, today.month

    start = dt.date(year, month, 1)
    end = (start + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    days = CalendarDay.objects.between(start, end).order_by("date")

    return render(
        request,
        "dormcalendar/index.html",
        {
            "days": days,
            "year": year,
            "month": month,
            "statuses": DayStatus.choices,
            "school_year": SchoolYear.current(),
        },
    )


@login_required
@require_POST
@require_capabilities(Capability.MANAGE_CALENDAR)
def update_day(request, day_id):
    day = get_object_or_404(CalendarDay, pk=day_id)
    try:
        update_calendar_day(
            day=day,
            actor=request.user,
            status=request.POST.get("status") or None,
            evening_check_required=request.POST.get("evening_check_required") == "1",
            note=request.POST.get("note"),
        )
        messages.success(request, f"{day.date:%Y-%m-%d} frissítve.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, " ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
    return redirect(f"/calendar/?year={day.date.year}&month={day.date.month}")


@login_required
@require_POST
@require_capabilities(Capability.MANAGE_CALENDAR)
def generate(request):
    created = ensure_calendar_days()
    messages.success(request, f"{created} naptári nap létrehozva.")
    return redirect("dormcalendar:index")

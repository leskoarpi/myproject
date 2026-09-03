"""Dashboards.

The dashboard is a *presentation* of data assembled by selectors and
services; it contains no business rules of its own (spec section 42).
"""

import datetime as dt

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.capabilities import Capability
from apps.core.models import ModuleKey, SchoolYear, SystemModule
from apps.dormcalendar.services import today_day
from apps.inspections.models import InspectionState, MorningSnapshot, RoomCheckSession
from apps.inspections.services.evening import evening_overview
from apps.leave_permissions.models import LeavePermission
from apps.presence.models import StatusType
from apps.presence.services import ensure_presence_row, presence_summary
from apps.students.models import ChangeRequestStatus, StudentChangeRequest
from apps.students.selectors import student_queryset_for_user
from apps.weekend.models import StayStatus, WeekendStay, friday_of


@login_required
def dashboard(request):
    if request.user.is_student:
        return _student_dashboard(request)
    return _staff_dashboard(request)


def _student_dashboard(request):
    student = getattr(request.user, "student_profile", None)
    if student is None:
        return render(request, "portal/no_profile.html", status=200)

    presence = ensure_presence_row(student)
    weekend_start = friday_of(timezone.localdate())

    context = {
        "student": student,
        "presence": presence,
        "room": student.current_room,
        "leave_permission": LeavePermission.objects.filter(student=student).first(),
        "leave_module_on": SystemModule.is_module_enabled(ModuleKey.LEAVE_PERMISSIONS),
        "weekend_module_on": SystemModule.is_module_enabled(ModuleKey.WEEKEND_STAY),
        "weekend_start": weekend_start,
        "weekend_stay": WeekendStay.objects.filter(
            student=student, weekend_start=weekend_start
        ).first(),
        "leave_statuses": StatusType.objects.student_selectable().filter(
            counts_as_inside=False
        ),
        "inside_status": StatusType.objects.filter(
            counts_as_inside=True, is_active=True
        ).first(),
        "greeting": _greeting(),
    }
    return render(request, "portal/dashboard_student.html", context)


def _greeting():
    hour = timezone.localtime().hour
    if hour < 10:
        return "Jó reggelt"
    if hour < 18:
        return "Jó napot"
    return "Jó estét"


def _staff_dashboard(request):
    user = request.user
    students = student_queryset_for_user(user)
    day = today_day()
    today = day.date

    context = {
        "day": day,
        "school_year": SchoolYear.current(),
        "summary": presence_summary(students)
        if user.has_capability(Capability.VIEW_PRESENCE)
        else None,
        "greeting": _greeting(),
    }

    if user.has_capability(Capability.VIEW_EVENING_CHECK) and SystemModule.is_module_enabled(
        ModuleKey.EVENING_CHECK
    ):
        context["evening"] = evening_overview(day)

    if user.has_capability(Capability.VIEW_MORNING_CHECK) and SystemModule.is_module_enabled(
        ModuleKey.MORNING_CHECK
    ):
        context["morning"] = (
            MorningSnapshot.objects.filter(calendar_day__date=today)
            .select_related("calendar_day")
            .first()
        )

    if user.has_capability(Capability.VIEW_ROOM_CHECKS) and SystemModule.is_module_enabled(
        ModuleKey.ROOM_CHECKS
    ):
        context["room_sessions"] = RoomCheckSession.objects.filter(
            date__gte=today - dt.timedelta(days=7)
        ).order_by("-date", "floor")[:10]
        context["open_room_sessions"] = RoomCheckSession.objects.filter(
            state=InspectionState.OPEN
        ).count()

    if user.has_capability(Capability.VIEW_WEEKEND_STAY) and SystemModule.is_module_enabled(
        ModuleKey.WEEKEND_STAY
    ):
        weekend_start = friday_of(today)
        context["weekend_start"] = weekend_start
        context["weekend_pending"] = WeekendStay.objects.filter(
            weekend_start=weekend_start, status=StayStatus.PENDING
        ).count()
        context["weekend_approved"] = WeekendStay.objects.filter(
            weekend_start=weekend_start, status=StayStatus.APPROVED
        ).count()

    if user.has_capability(Capability.REVIEW_STUDENT_CHANGES):
        context["pending_change_requests"] = StudentChangeRequest.objects.filter(
            status=ChangeRequestStatus.PENDING
        ).count()

    return render(request, "portal/dashboard_staff.html", context)

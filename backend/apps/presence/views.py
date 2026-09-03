from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.capabilities import Capability
from apps.accounts.permissions import require_capabilities
from apps.students.selectors import student_queryset_for_user

from .models import PresenceEvent, StatusType, StudentPresence
from .services import (
    change_student_presence,
    ensure_presence_row,
    presence_summary,
    student_leave,
    student_return,
)


@login_required
@require_capabilities(Capability.VIEW_PRESENCE)
def current_presence(request):
    """Live roster. The table polls itself every 30s (spec section 50);
    no websocket infrastructure is introduced for this."""
    students = student_queryset_for_user(request.user)
    search = request.GET.get("q", "").strip()
    status_code = request.GET.get("status", "").strip()
    floor = request.GET.get("floor", "").strip()

    presences = (
        StudentPresence.objects.filter(student__in=students)
        .select_related("student", "student__group", "status", "changed_by")
        .prefetch_related("student__room_assignments__room")
    )
    if search:
        presences = presences.filter(
            Q(student__full_name__icontains=search)
            | Q(student__room_assignments__room__number__icontains=search)
        ).distinct()
    if status_code:
        presences = presences.filter(status__code=status_code)
    if floor.isdigit():
        presences = presences.filter(
            student__room_assignments__is_active=True,
            student__room_assignments__room__floor=int(floor),
        )

    context = {
        "presences": presences.order_by("student__full_name"),
        "summary": presence_summary(students),
        "statuses": StatusType.objects.active(),
        "search": search,
        "selected_status": status_code,
        "selected_floor": floor,
        "can_edit": request.user.has_capability(Capability.EDIT_PRESENCE),
    }
    if request.headers.get("X-Partial"):
        return render(request, "presence/_current_table.html", context)
    return render(request, "presence/current.html", context)


@login_required
@require_capabilities(Capability.VIEW_PRESENCE_HISTORY)
def presence_log(request):
    students = student_queryset_for_user(request.user)
    events = (
        PresenceEvent.objects.filter(student__in=students)
        .select_related("student", "old_status", "new_status", "created_by")
    )
    search = request.GET.get("q", "").strip()
    if search:
        events = events.filter(student__full_name__icontains=search)
    source = request.GET.get("source", "").strip()
    if source:
        events = events.filter(source=source)

    paginator = Paginator(events, 50)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "presence/log.html",
        {"page": page, "search": search, "selected_source": source},
    )


@login_required
@require_capabilities(Capability.VIEW_PRESENCE)
def student_presence_detail(request, student_id):
    student = get_object_or_404(student_queryset_for_user(request.user), pk=student_id)
    presence = ensure_presence_row(student)
    events = (
        PresenceEvent.objects.filter(student=student)
        .select_related("old_status", "new_status", "created_by")[:100]
    )
    return render(
        request,
        "presence/student_detail.html",
        {
            "student": student,
            "presence": presence,
            "events": events,
            "statuses": StatusType.objects.active(),
            "can_edit": request.user.has_capability(Capability.EDIT_PRESENCE),
        },
    )


@login_required
@require_POST
def set_student_presence(request, student_id):
    """Staff changes another student's status."""
    student = get_object_or_404(student_queryset_for_user(request.user), pk=student_id)
    status_code = request.POST.get("status", "")
    reason = request.POST.get("reason", "")[:255]

    try:
        change_student_presence(
            student=student, new_status=status_code, actor=request.user, reason=reason
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _message(exc))
    else:
        messages.success(request, f"{student.full_name} státusza frissítve.")

    if request.headers.get("X-Partial"):
        presence = ensure_presence_row(student)
        return render(
            request,
            "presence/_row.html",
            {"presence": presence, "statuses": StatusType.objects.active(), "can_edit": True},
        )
    return redirect(request.POST.get("next") or "presence:current")


# --------------------------------------------------------------------------
# Student self-service (spec section 12)
# --------------------------------------------------------------------------


@login_required
@require_POST
def self_return(request):
    student = _own_profile(request)
    try:
        student_return(student=student, actor=request.user)
        messages.success(request, "Rögzítettük: bejöttél.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _message(exc))
    return redirect("portal:dashboard")


@login_required
@require_POST
def self_leave(request):
    student = _own_profile(request)
    status_code = request.POST.get("status", "")
    reason = request.POST.get("reason", "")[:255]
    try:
        student_leave(
            student=student, actor=request.user, status_code=status_code, reason=reason
        )
        messages.success(request, "Rögzítettük: kimentél.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _message(exc))
    return redirect("portal:dashboard")


def _own_profile(request):
    profile = getattr(request.user, "student_profile", None)
    if profile is None:
        raise PermissionDenied("This account has no student profile.")
    return profile


def _message(exc):
    if isinstance(exc, ValidationError):
        return " ".join(exc.messages)
    return str(exc)

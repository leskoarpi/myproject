import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.capabilities import Capability
from apps.accounts.permissions import require_capabilities, require_module
from apps.core.models import ModuleKey
from apps.students.selectors import student_queryset_for_user

from .models import LeavePermission, LeavePermissionHistory, PermissionStatus, PermissionType
from .services import (
    grant_leave_permission,
    leave_permission_queryset_for_user,
    revoke_leave_permission,
)


def _msg(exc):
    return " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


@login_required
@require_module(ModuleKey.LEAVE_PERMISSIONS)
@require_capabilities(Capability.VIEW_LEAVE_PERMISSIONS)
def index(request):
    permissions = leave_permission_queryset_for_user(request.user)
    status = request.GET.get("status", "")
    if status:
        permissions = permissions.filter(status=status)
    search = request.GET.get("q", "").strip()
    if search:
        permissions = permissions.filter(student__full_name__icontains=search)

    students_without = student_queryset_for_user(request.user).filter(
        leave_permission__isnull=True
    )

    return render(
        request,
        "leave/index.html",
        {
            "permissions": permissions.order_by("student__full_name"),
            "students_without": students_without,
            "statuses": PermissionStatus.choices,
            "types": PermissionType.choices,
            "selected_status": status,
            "search": search,
            "can_manage": request.user.has_capability(Capability.MANAGE_LEAVE_PERMISSIONS),
        },
    )


@login_required
@require_POST
@require_module(ModuleKey.LEAVE_PERMISSIONS)
def grant(request, student_id):
    student = get_object_or_404(student_queryset_for_user(request.user), pk=student_id)
    expires_raw = request.POST.get("expires_at", "").strip()
    expires_at = None
    if expires_raw:
        try:
            parsed = dt.datetime.fromisoformat(expires_raw)
        except ValueError:
            messages.error(request, "Érvénytelen lejárati időpont.")
            return redirect("leave:index")
        expires_at = timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed

    try:
        grant_leave_permission(
            student=student,
            actor=request.user,
            permission_type=request.POST.get("permission_type", PermissionType.STANDING),
            value=request.POST.get("value", ""),
            expires_at=expires_at,
            note=request.POST.get("note", ""),
        )
        messages.success(request, f"{student.full_name} engedélye rögzítve.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect("leave:index")


@login_required
@require_POST
@require_module(ModuleKey.LEAVE_PERMISSIONS)
def revoke(request, student_id):
    student = get_object_or_404(student_queryset_for_user(request.user), pk=student_id)
    try:
        revoke_leave_permission(
            student=student, actor=request.user, note=request.POST.get("note", "")
        )
        messages.success(request, f"{student.full_name} engedélye visszavonva.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect("leave:index")


@login_required
@require_module(ModuleKey.LEAVE_PERMISSIONS)
@require_capabilities(Capability.VIEW_LEAVE_PERMISSIONS)
def history(request):
    students = student_queryset_for_user(request.user)
    entries = LeavePermissionHistory.objects.filter(student__in=students).select_related(
        "student", "created_by"
    )
    paginator = Paginator(entries, 50)
    return render(
        request,
        "leave/history.html",
        {"page": paginator.get_page(request.GET.get("page"))},
    )


@login_required
@require_module(ModuleKey.LEAVE_PERMISSIONS)
@require_capabilities(Capability.VIEW_OWN_LEAVE_PERMISSION)
def my_permission(request):
    student = getattr(request.user, "student_profile", None)
    if student is None:
        raise PermissionDenied("This account has no student profile.")
    return render(
        request,
        "leave/mine.html",
        {
            "permission": LeavePermission.objects.filter(student=student).first(),
            "history": LeavePermissionHistory.objects.filter(student=student)[:20],
        },
    )

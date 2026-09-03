import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.capabilities import Capability
from apps.accounts.permissions import require_capabilities, require_module
from apps.core.models import ModuleKey
from apps.rooms.models import Room

from .models import (
    StayStatus,
    WeekendCheckResult,
    WeekendCheckSession,
    WeekendCheckType,
    WeekendStay,
    friday_of,
)
from .services import (
    cancel_weekend_stay,
    close_weekend_check_session,
    create_guest_stay,
    open_weekend_check_session,
    record_weekend_check,
    reopen_weekend_check_session,
    review_weekend_stay,
    submit_weekend_stay,
    weekend_roster,
    weekend_stay_queryset_for_user,
)


def _msg(exc):
    return " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


def _weekend_from_request(request):
    raw = request.GET.get("weekend") or request.POST.get("weekend")
    if raw:
        try:
            return friday_of(dt.date.fromisoformat(raw))
        except ValueError:
            pass
    return friday_of(timezone.localdate())


@login_required
@require_module(ModuleKey.WEEKEND_STAY)
@require_capabilities(Capability.VIEW_WEEKEND_STAY)
def index(request):
    weekend_start = _weekend_from_request(request)
    stays = weekend_stay_queryset_for_user(request.user, weekend_start=weekend_start)
    status = request.GET.get("status", "")
    if status:
        stays = stays.filter(status=status)

    sessions = {
        s.check_type: s for s in WeekendCheckSession.objects.filter(weekend_start=weekend_start)
    }
    return render(
        request,
        "weekend/index.html",
        {
            "weekend_start": weekend_start,
            "prev_weekend": weekend_start - dt.timedelta(days=7),
            "next_weekend": weekend_start + dt.timedelta(days=7),
            "stays": stays,
            "statuses": StayStatus.choices,
            "selected_status": status,
            "check_types": WeekendCheckType.choices,
            "sessions": sessions,
            "pending_count": stays.filter(status=StayStatus.PENDING).count(),
            "can_review": request.user.has_capability(Capability.REVIEW_WEEKEND_STAY),
            "can_manage": request.user.has_capability(Capability.MANAGE_WEEKEND_STAY),
            "can_check": request.user.has_capability(Capability.RUN_WEEKEND_CHECK),
            "rooms": Room.objects.active(),
        },
    )


@login_required
@require_module(ModuleKey.WEEKEND_STAY)
@require_capabilities(Capability.REQUEST_WEEKEND_STAY)
def my_stay(request):
    """Student self-service registration."""
    student = getattr(request.user, "student_profile", None)
    if student is None:
        raise PermissionDenied("This account has no student profile.")

    weekend_start = friday_of(timezone.localdate())
    if request.method == "POST":
        try:
            submit_weekend_stay(
                student=student,
                actor=request.user,
                weekend_start=weekend_start,
                friday_stay=bool(request.POST.get("friday_stay")),
                saturday_stay=bool(request.POST.get("saturday_stay")),
                note=request.POST.get("note", ""),
            )
            messages.success(request, "A hétvégi bennmaradási kérelmedet rögzítettük.")
            return redirect("weekend:my_stay")
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, _msg(exc))

    return render(
        request,
        "weekend/my_stay.html",
        {
            "weekend_start": weekend_start,
            "stay": WeekendStay.objects.filter(
                student=student, weekend_start=weekend_start
            ).first(),
            "history": WeekendStay.objects.filter(student=student).order_by("-weekend_start")[:10],
        },
    )


@login_required
@require_POST
@require_module(ModuleKey.WEEKEND_STAY)
def review(request, stay_id):
    stay = get_object_or_404(weekend_stay_queryset_for_user(request.user), pk=stay_id)
    approve = request.POST.get("decision") == "approve"
    try:
        review_weekend_stay(
            stay=stay, actor=request.user, approve=approve, note=request.POST.get("note", "")
        )
        messages.success(request, "Elbírálva.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect(f"{reverse('weekend:index')}?weekend={stay.weekend_start}")


@login_required
@require_POST
@require_module(ModuleKey.WEEKEND_STAY)
def cancel(request, stay_id):
    stay = get_object_or_404(weekend_stay_queryset_for_user(request.user), pk=stay_id)
    try:
        cancel_weekend_stay(stay=stay, actor=request.user)
        messages.success(request, "Visszavonva.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect("weekend:my_stay" if request.user.is_student else "weekend:index")


@login_required
@require_POST
@require_module(ModuleKey.WEEKEND_STAY)
@require_capabilities(Capability.MANAGE_WEEKEND_STAY)
def add_guest(request):
    weekend_start = _weekend_from_request(request)
    room_id = request.POST.get("room")
    try:
        create_guest_stay(
            actor=request.user,
            weekend_start=weekend_start,
            guest_name=request.POST.get("guest_name", "").strip(),
            room=Room.objects.filter(pk=room_id).first() if room_id else None,
            friday_stay=bool(request.POST.get("friday_stay")),
            saturday_stay=bool(request.POST.get("saturday_stay")),
            guest_phone=request.POST.get("guest_phone", ""),
            guest_email=request.POST.get("guest_email", ""),
            guest_guardian_name=request.POST.get("guest_guardian_name", ""),
            guest_guardian_phone=request.POST.get("guest_guardian_phone", ""),
            guest_guardian_email=request.POST.get("guest_guardian_email", ""),
            note=request.POST.get("note", ""),
        )
        messages.success(request, "Vendég hozzáadva.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect(f"{reverse('weekend:index')}?weekend={weekend_start}")


@login_required
@require_module(ModuleKey.WEEKEND_STAY)
@require_capabilities(Capability.RUN_WEEKEND_CHECK)
def check_session(request, weekend, check_type):
    weekend_start = friday_of(dt.date.fromisoformat(weekend))
    if check_type not in WeekendCheckType.values:
        raise PermissionDenied("Unknown check type.")

    session = open_weekend_check_session(
        weekend_start=weekend_start, check_type=check_type, actor=request.user
    )
    checks = session.checks.select_related("stay", "checked_by")
    return render(
        request,
        "weekend/check_session.html",
        {
            "session": session,
            "checks": checks,
            "results": WeekendCheckResult.choices,
            "can_edit": session.is_open,
            "can_reopen": request.user.has_capability(Capability.REOPEN_WEEKEND_CHECK),
        },
    )


@login_required
@require_POST
@require_module(ModuleKey.WEEKEND_STAY)
def record_check(request, session_id, stay_id):
    session = get_object_or_404(WeekendCheckSession, pk=session_id)
    stay = get_object_or_404(WeekendStay, pk=stay_id, weekend_start=session.weekend_start)
    try:
        record_weekend_check(
            session=session,
            stay=stay,
            actor=request.user,
            result=request.POST.get("result", ""),
            note=request.POST.get("note", ""),
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect(
        "weekend:check_session", weekend=session.weekend_start, check_type=session.check_type
    )


@login_required
@require_POST
@require_module(ModuleKey.WEEKEND_STAY)
def close_check(request, session_id):
    session = get_object_or_404(WeekendCheckSession, pk=session_id)
    try:
        close_weekend_check_session(session=session, actor=request.user)
        messages.success(request, "Ellenőrzés lezárva.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect(
        "weekend:check_session", weekend=session.weekend_start, check_type=session.check_type
    )


@login_required
@require_POST
@require_module(ModuleKey.WEEKEND_STAY)
def reopen_check(request, session_id):
    session = get_object_or_404(WeekendCheckSession, pk=session_id)
    try:
        reopen_weekend_check_session(
            session=session, actor=request.user, reason=request.POST.get("reason", "")
        )
        messages.success(request, "Ellenőrzés újranyitva.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect(
        "weekend:check_session", weekend=session.weekend_start, check_type=session.check_type
    )


@login_required
@require_module(ModuleKey.WEEKEND_STAY)
@require_capabilities(Capability.VIEW_WEEKEND_STAY)
def roster(request, weekend):
    """A4 landscape roster, as HTML or PDF (spec section 35)."""
    weekend_start = friday_of(dt.date.fromisoformat(weekend))
    rows = weekend_roster(weekend_start)

    if request.GET.get("format") == "pdf":
        from apps.reports.pdf import weekend_roster_pdf

        pdf_bytes = weekend_roster_pdf(weekend_start, rows)
        if pdf_bytes is None:
            messages.warning(
                request, "A PDF előállítás nem érhető el; a nyomtatható nézet jelenik meg."
            )
        else:
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = (
                f'inline; filename="hetvege-{weekend_start:%Y-%m-%d}.pdf"'
            )
            return response

    return render(
        request,
        "weekend/roster.html",
        {"weekend_start": weekend_start, "rows": rows},
    )

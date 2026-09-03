from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.capabilities import Capability
from apps.accounts.permissions import require_capabilities
from apps.audit.services import AuditAction, record_audit
from apps.dormcalendar.services import ensure_calendar_days

from .models import SchoolYear, SystemModule


class SchoolYearForm(forms.ModelForm):
    class Meta:
        model = SchoolYear
        fields = ["name", "start_date", "end_date", "is_active"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }


def _msg(exc):
    return " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


@login_required
@require_capabilities(Capability.MANAGE_MODULES)
def modules(request):
    return render(
        request,
        "core/modules.html",
        {"modules": SystemModule.objects.all()},
    )


@login_required
@require_POST
@require_capabilities(Capability.MANAGE_MODULES)
def toggle_module(request, module_id):
    module = get_object_or_404(SystemModule, pk=module_id)
    previous = module.is_enabled
    module.is_enabled = not previous
    module.save(update_fields=["is_enabled", "updated_at"])
    record_audit(
        user=request.user,
        action=AuditAction.UPDATE,
        target=module,
        old_value={"is_enabled": previous},
        new_value={"is_enabled": module.is_enabled},
    )
    messages.success(
        request,
        f"{module.name} {'bekapcsolva' if module.is_enabled else 'kikapcsolva'}. "
        "A meglévő adatok érintetlenek maradtak.",
    )
    return redirect("core:modules")


@login_required
@require_capabilities(Capability.MANAGE_SETTINGS)
def school_years(request):
    if request.method == "POST":
        form = SchoolYearForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                if form.cleaned_data["is_active"]:
                    SchoolYear.objects.filter(is_active=True).update(is_active=False)
                year = form.save()
                ensure_calendar_days(year)
                record_audit(user=request.user, action=AuditAction.CREATE, target=year)
            messages.success(request, "Tanév létrehozva, naptár feltöltve.")
            return redirect("core:school_years")
    else:
        form = SchoolYearForm()

    return render(
        request,
        "core/school_years.html",
        {"years": SchoolYear.objects.all(), "form": form},
    )


@login_required
@require_POST
@require_capabilities(Capability.MANAGE_SETTINGS)
def activate_school_year(request, year_id):
    year = get_object_or_404(SchoolYear, pk=year_id)
    with transaction.atomic():
        SchoolYear.objects.filter(is_active=True).exclude(pk=year.pk).update(is_active=False)
        year.is_active = True
        year.save(update_fields=["is_active", "updated_at"])
        ensure_calendar_days(year)
        record_audit(
            user=request.user, action=AuditAction.UPDATE, target=year, note="activated"
        )
    messages.success(request, f"{year.name} az aktív tanév.")
    return redirect("core:school_years")


@login_required
@require_capabilities(Capability.MANAGE_MAINTENANCE)
def maintenance(request):
    """Administrative maintenance.

    Destructive operations are refused unless the deployment explicitly opts
    in via ``ALLOW_DESTRUCTIVE_MAINTENANCE`` *and* the operator types the
    confirmation phrase. Archival is offered as the safe default
    (spec section 49).
    """
    from apps.audit.models import AuditLog
    from apps.presence.models import PresenceEvent
    from apps.students.models import StudentProfile

    return render(
        request,
        "core/maintenance.html",
        {
            "destructive_allowed": settings.ALLOW_DESTRUCTIVE_MAINTENANCE,
            "can_delete_all": request.user.has_capability(Capability.DELETE_ALL_DATA),
            "counts": {
                "students": StudentProfile.objects.count(),
                "archived_students": StudentProfile.objects.archived().count(),
                "presence_events": PresenceEvent.objects.count(),
                "audit_entries": AuditLog.objects.count(),
            },
        },
    )


@login_required
@require_POST
@require_capabilities(Capability.MANAGE_MAINTENANCE, Capability.DELETE_ALL_DATA)
def maintenance_reset(request):
    """Deliberately hard to trigger, and never available by default."""
    if not settings.ALLOW_DESTRUCTIVE_MAINTENANCE:
        record_audit(
            user=request.user,
            action=AuditAction.MAINTENANCE,
            target_type="system",
            target_repr="reset refused",
            note="ALLOW_DESTRUCTIVE_MAINTENANCE is off",
        )
        messages.error(
            request,
            "A destruktív műveletek ki vannak kapcsolva ebben a környezetben "
            "(ALLOW_DESTRUCTIVE_MAINTENANCE=false).",
        )
        return redirect("core:maintenance")

    if request.POST.get("confirmation") != "TOROL MINDENT":
        messages.error(request, "A megerősítő szöveg nem egyezik. Nem történt változás.")
        return redirect("core:maintenance")

    record_audit(
        user=request.user,
        action=AuditAction.MAINTENANCE,
        target_type="system",
        target_repr="operational data reset",
        note="confirmed destructive reset",
    )

    from apps.inspections.models import (
        EveningCheckResult,
        EveningCheckSession,
        MorningSnapshot,
        MorningSnapshotItem,
        RoomCheck,
        RoomCheckSession,
    )
    from apps.presence.models import PresenceEvent
    from apps.weekend.models import WeekendCheck, WeekendCheckSession, WeekendStay

    with transaction.atomic():
        # Student and room records are archived, never dropped: history has to
        # stay valid. Only operational transaction data is cleared.
        WeekendCheck.objects.all().delete()
        WeekendCheckSession.objects.all().delete()
        WeekendStay.objects.all().delete()
        MorningSnapshotItem.objects.all().delete()
        MorningSnapshot.objects.all().delete()
        EveningCheckResult.objects.all().delete()
        EveningCheckSession.objects.all().delete()
        RoomCheck.objects.all().delete()
        RoomCheckSession.objects.all().delete()
        PresenceEvent.objects.all().delete()

    messages.warning(request, "Az operatív ellenőrzési adatok törölve. A diákok megmaradtak.")
    return redirect("core:maintenance")

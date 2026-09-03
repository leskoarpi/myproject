from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.capabilities import Capability
from apps.accounts.permissions import require_capabilities
from apps.audit.services import AuditAction, record_audit

from . import csv_io
from .models import Room
from .services import save_room

PREVIEW_SESSION_KEY = "room_import_preview"


def _msg(exc):
    return " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ["number", "floor", "capacity", "notes", "is_active"]


@login_required
@require_capabilities(Capability.VIEW_ROOMS)
def room_list(request):
    rooms = Room.objects.annotate(
        occupancy_count=Count("assignments", filter=Q(assignments__is_active=True))
    )
    search = request.GET.get("q", "").strip()
    if search:
        rooms = rooms.filter(number__icontains=search)
    floor = request.GET.get("floor", "")
    if floor.isdigit():
        rooms = rooms.filter(floor=int(floor))

    return render(
        request,
        "rooms/list.html",
        {
            "rooms": rooms.order_by("floor", "number"),
            "search": search,
            "selected_floor": floor,
            "floors": Room.objects.values_list("floor", flat=True).distinct().order_by("floor"),
            "can_manage": request.user.has_capability(Capability.MANAGE_ROOMS),
            "can_import": request.user.has_capability(Capability.IMPORT_DATA),
            "can_export": request.user.has_capability(Capability.EXPORT_DATA),
        },
    )


@login_required
@require_capabilities(Capability.MANAGE_ROOMS)
def room_form(request, room_id=None):
    room = get_object_or_404(Room, pk=room_id) if room_id else None
    if request.method == "POST":
        form = RoomForm(request.POST, instance=room)
        if form.is_valid():
            try:
                save_room(actor=request.user, room=room, **form.cleaned_data)
                messages.success(request, "Mentve.")
                return redirect("rooms:list")
            except (PermissionDenied, ValidationError) as exc:
                messages.error(request, _msg(exc))
    else:
        form = RoomForm(instance=room)
    return render(request, "rooms/form.html", {"form": form, "room": room})


@login_required
@require_capabilities(Capability.VIEW_ROOMS)
def room_detail(request, room_id):
    room = get_object_or_404(Room, pk=room_id)
    return render(
        request,
        "rooms/detail.html",
        {
            "room": room,
            "current": room.assignments.filter(is_active=True).select_related("student"),
            "history": room.assignments.select_related("student", "school_year")[:50],
        },
    )


# --------------------------------------------------------------------------
# CSV (spec section 44)
# --------------------------------------------------------------------------


@login_required
@require_capabilities(Capability.EXPORT_DATA)
def room_export(request):
    payload = csv_io.export_csv()
    record_audit(
        user=request.user,
        action=AuditAction.EXPORT,
        target_type="rooms.Room",
        target_repr="Room CSV export",
    )
    response = HttpResponse(payload, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="szobak.csv"'
    return response


@login_required
@require_capabilities(Capability.IMPORT_DATA)
def room_template(request):
    response = HttpResponse(csv_io.template_csv(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="szobak-sablon.csv"'
    return response


@login_required
@require_capabilities(Capability.IMPORT_DATA)
def room_import(request):
    """Step 1: upload and validate; nothing is written yet."""
    if request.method == "POST" and request.FILES.get("file"):
        try:
            preview = csv_io.build_preview(request.FILES["file"])
        except ValidationError as exc:
            messages.error(request, _msg(exc))
            return redirect("rooms:import")

        request.session[PREVIEW_SESSION_KEY] = preview.to_session()
        return render(request, "rooms/import_preview.html", {"preview": preview})

    return render(request, "rooms/import.html", {})


@login_required
@require_POST
@require_capabilities(Capability.IMPORT_DATA)
def room_import_apply(request):
    """Step 2: apply the reviewed preview inside one transaction."""
    rows = request.session.get(PREVIEW_SESSION_KEY)
    if not rows:
        messages.error(request, "Az import előnézet lejárt. Töltsd fel újra a fájlt.")
        return redirect("rooms:import")

    overwrite_ids = request.POST.getlist("overwrite")
    try:
        summary = csv_io.apply_import(
            rows=rows, actor=request.user, overwrite_ids=overwrite_ids
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
        return redirect("rooms:import")

    request.session.pop(PREVIEW_SESSION_KEY, None)
    messages.success(
        request,
        f"Import kész: {summary['created']} új, {summary['updated']} frissítve, "
        f"{summary['skipped']} kihagyva.",
    )
    return redirect("rooms:list")

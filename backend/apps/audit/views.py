from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render

from apps.accounts.capabilities import Capability
from apps.accounts.permissions import require_capabilities

from .models import AuditAction, AuditLog


@login_required
@require_capabilities(Capability.VIEW_AUDIT_LOG)
def index(request):
    entries = AuditLog.objects.select_related("user")
    search = request.GET.get("q", "").strip()
    if search:
        entries = entries.filter(
            Q(username__icontains=search)
            | Q(target_repr__icontains=search)
            | Q(target_type__icontains=search)
        )
    action = request.GET.get("action", "")
    if action:
        entries = entries.filter(action=action)

    paginator = Paginator(entries, 50)
    return render(
        request,
        "audit/index.html",
        {
            "page": paginator.get_page(request.GET.get("page")),
            "actions": AuditAction.choices,
            "search": search,
            "selected_action": action,
        },
    )

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.capabilities import Capability
from apps.accounts.permissions import require_capabilities, require_module
from apps.core.models import ModuleKey
from apps.students.selectors import presence_queryset_for_user, student_queryset_for_user

from .models import PassEligibility, PassRuleHistory
from .services import (
    can_set_pass_rule,
    ensure_rules_exist,
    pass_rule_queryset_for_user,
    set_pass_rule,
)


def _msg(exc):
    return " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


@login_required
@require_module(ModuleKey.PASS_RULES)
@require_capabilities(Capability.VIEW_PASS_RULES)
def index(request):
    """Every teacher and director sees the whole dormitory here.

    Whoever is on duty is the one being asked for a pass, so the list is not
    scoped to the viewer's own group. Setting a rule still is.
    """
    # Make sure every student has a row, so nobody is missing from the list.
    ensure_rules_exist(list(presence_queryset_for_user(request.user)))

    rules = pass_rule_queryset_for_user(request.user)

    eligibility = request.GET.get("eligibility", "").strip()
    if eligibility:
        rules = rules.filter(eligibility=eligibility)
    search = request.GET.get("q", "").strip()
    if search:
        rules = rules.filter(
            Q(student__full_name__icontains=search) | Q(note__icontains=search)
        )
    if request.GET.get("restricted") == "1":
        rules = rules.restricted()

    editable_ids = set(
        student_queryset_for_user(request.user).values_list("pk", flat=True)
    ) if request.user.has_capability(Capability.MANAGE_PASS_RULES) else set()

    paginator = Paginator(rules.order_by("student__full_name"), 100)
    return render(
        request,
        "leave/index.html",
        {
            "page": paginator.get_page(request.GET.get("page")),
            "choices": PassEligibility.choices,
            "selected": eligibility,
            "search": search,
            "restricted_only": request.GET.get("restricted") == "1",
            "editable_ids": editable_ids,
            "can_manage": request.user.has_capability(Capability.MANAGE_PASS_RULES),
        },
    )


@login_required
@require_POST
@require_module(ModuleKey.PASS_RULES)
def set_rule(request, student_id):
    # Writing stays scoped: the student must be in the actor's own set.
    student = get_object_or_404(student_queryset_for_user(request.user), pk=student_id)
    try:
        set_pass_rule(
            student=student,
            actor=request.user,
            eligibility=request.POST.get("eligibility", ""),
            note=request.POST.get("note", ""),
        )
        messages.success(request, f"{student.full_name} kimenő jogosultsága frissítve.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect(request.POST.get("next") or "passes:index")


@login_required
@require_module(ModuleKey.PASS_RULES)
@require_capabilities(Capability.VIEW_PASS_RULES)
def history(request):
    entries = PassRuleHistory.objects.select_related("student", "created_by")
    search = request.GET.get("q", "").strip()
    if search:
        entries = entries.filter(student__full_name__icontains=search)
    paginator = Paginator(entries, 50)
    return render(
        request,
        "leave/history.html",
        {"page": paginator.get_page(request.GET.get("page")), "search": search},
    )


@login_required
@require_module(ModuleKey.PASS_RULES)
@require_capabilities(Capability.VIEW_OWN_PASS_RULE)
def my_rule(request):
    student = getattr(request.user, "student_profile", None)
    if student is None:
        raise PermissionDenied("This account has no student profile.")
    from .services import rule_for

    return render(
        request,
        "leave/mine.html",
        {
            "rule": rule_for(student),
            "history": PassRuleHistory.objects.filter(student=student)[:20],
        },
    )


@login_required
@require_module(ModuleKey.PASS_RULES)
@require_capabilities(Capability.VIEW_PASS_RULES)
def student_rule(request, student_id):
    """The rule for one student, with its change history."""
    student = get_object_or_404(presence_queryset_for_user(request.user), pk=student_id)
    from .services import rule_for

    return render(
        request,
        "leave/student.html",
        {
            "student": student,
            "rule": rule_for(student),
            "choices": PassEligibility.choices,
            "can_edit": can_set_pass_rule(request.user, student),
            "history": PassRuleHistory.objects.filter(student=student)[:30],
        },
    )

"""Pass-rule services.

Two deliberately different scopes here (spec section 58):

* **Reading** is dormitory-wide. Any teacher or director may look up any
  student's pass rule, because whoever is on duty is the one being asked for a
  pass.
* **Writing** stays with the student's own teachers and with management.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.capabilities import Capability
from apps.audit.services import AuditAction, record_audit
from apps.core.models import ModuleKey, SystemModule

from .models import PassEligibility, PassRuleHistory, StudentPassRule


def _require_module():
    if not SystemModule.is_module_enabled(ModuleKey.PASS_RULES):
        raise PermissionDenied("The pass rule module is disabled.")


def pass_rule_queryset_for_user(user):
    """Pass rules ``user`` may read.

    Staff see the whole dormitory; a student sees only their own.
    """
    base = StudentPassRule.objects.select_related("student", "student__group", "set_by")
    if not user or not user.is_authenticated:
        return base.none()
    if user.has_capability(Capability.VIEW_PASS_RULES):
        return base.filter(student__is_active=True)
    if user.has_capability(Capability.VIEW_OWN_PASS_RULE):
        profile = getattr(user, "student_profile", None)
        return base.filter(student=profile) if profile else base.none()
    return base.none()


def can_set_pass_rule(user, student):
    """Writing stays scoped to the student's own teachers and to management."""
    if not user or not user.is_authenticated:
        return False
    if not user.has_capability(Capability.MANAGE_PASS_RULES):
        return False

    from apps.students.selectors import student_queryset_for_user

    return student_queryset_for_user(user).filter(pk=student.pk).exists()


def rule_for(student):
    """The student's rule, created on first read so the UI always has a row."""
    rule, _ = StudentPassRule.objects.get_or_create(student=student)
    return rule


@transaction.atomic
def set_pass_rule(*, student, actor, eligibility, note=""):
    """Set a student's pass rule and append the change to the history."""
    _require_module()
    if not can_set_pass_rule(actor, student):
        raise PermissionDenied("You may not set this student's pass rule.")
    if eligibility not in PassEligibility.values:
        raise ValidationError(f"Unknown eligibility '{eligibility}'.")

    note = (note or "").strip()[:255]
    if eligibility == PassEligibility.OTHER and not note:
        raise ValidationError("Az „Egyéb” beállításhoz megjegyzés kell.")

    rule, _ = StudentPassRule.objects.select_for_update().get_or_create(student=student)
    before = {"eligibility": rule.eligibility, "note": rule.note}

    if before["eligibility"] == eligibility and before["note"] == note:
        return rule  # nothing changed, so nothing to record

    rule.eligibility = eligibility
    rule.note = note
    rule.set_by = actor
    rule.set_at = timezone.now()
    rule.full_clean(exclude=["student"])
    rule.save()

    PassRuleHistory.objects.create(
        student=student,
        previous_eligibility=before["eligibility"],
        previous_note=before["note"],
        new_eligibility=rule.eligibility,
        new_note=rule.note,
        created_by=actor if getattr(actor, "pk", None) else None,
    )
    record_audit(
        user=actor,
        action=AuditAction.UPDATE,
        target=rule,
        old_value=before,
        new_value={"eligibility": rule.eligibility, "note": rule.note},
    )
    return rule


def ensure_rules_exist(students):
    """Create missing rows for a list of students in one round trip."""
    existing = set(
        StudentPassRule.objects.filter(student__in=students).values_list(
            "student_id", flat=True
        )
    )
    missing = [StudentPassRule(student=s) for s in students if s.pk not in existing]
    if missing:
        StudentPassRule.objects.bulk_create(missing, ignore_conflicts=True)
    return len(missing)

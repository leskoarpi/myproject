"""Pass eligibility.

The dormitory runs its own paper pass system, so this application no longer
issues or expires passes. What it holds instead is a per-student *rule* saying
whether a pass may be given at all, and by whom - an attribute of the student
that every teacher and director can look up before handing one out.

The rule is current state; every change is appended to
:class:`PassRuleHistory` (spec section 70).
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class PassEligibility(models.TextChoices):
    ALLOWED = "allowed", "Kaphat kimenőt"
    TEACHER_ONLY = "teacher_only", "Csak a saját nevelőtanára adhat"
    DENIED = "denied", "Nem kaphat kimenőt"
    OTHER = "other", "Egyéb (lásd megjegyzés)"


class PassRuleQuerySet(models.QuerySet):
    def restricted(self):
        return self.exclude(eligibility=PassEligibility.ALLOWED)


class StudentPassRule(TimeStampedModel):
    """Whether this student may be given a pass, and by whom."""

    student = models.OneToOneField(
        "students.StudentProfile", on_delete=models.CASCADE, related_name="pass_rule"
    )
    eligibility = models.CharField(
        "beállítás",
        max_length=16,
        choices=PassEligibility.choices,
        default=PassEligibility.ALLOWED,
    )
    note = models.CharField(
        "megjegyzés",
        max_length=255,
        blank=True,
        help_text="Kötelező, ha az „Egyéb” beállítás van kiválasztva.",
    )
    set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    set_at = models.DateTimeField(default=timezone.now)

    objects = PassRuleQuerySet.as_manager()

    class Meta:
        verbose_name = "kimenő jogosultság"
        verbose_name_plural = "kimenő jogosultságok"
        ordering = ["student__full_name"]
        indexes = [models.Index(fields=["eligibility"])]
        constraints = [
            # "Egyéb" without an explanation would tell a colleague nothing.
            models.CheckConstraint(
                condition=~models.Q(eligibility=PassEligibility.OTHER)
                | ~models.Q(note=""),
                name="passrule_other_requires_note",
            )
        ]

    def __str__(self):
        return f"{self.student}: {self.get_eligibility_display()}"

    def clean(self):
        if self.eligibility == PassEligibility.OTHER and not self.note.strip():
            raise ValidationError({"note": "Az „Egyéb” beállításhoz megjegyzés kell."})

    @property
    def is_restricted(self):
        return self.eligibility != PassEligibility.ALLOWED

    @property
    def badge_class(self):
        return {
            PassEligibility.ALLOWED: "ok",
            PassEligibility.TEACHER_ONLY: "warn",
            PassEligibility.DENIED: "bad",
            PassEligibility.OTHER: "info",
        }.get(self.eligibility, "")


class PassRuleHistory(models.Model):
    """Append-only trail of pass-rule changes. Never rewritten."""

    student = models.ForeignKey(
        "students.StudentProfile",
        on_delete=models.PROTECT,
        related_name="pass_rule_history",
    )
    previous_eligibility = models.CharField(max_length=16, blank=True)
    previous_note = models.CharField(max_length=255, blank=True)
    new_eligibility = models.CharField(max_length=16, blank=True)
    new_note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["student", "-created_at"])]
        verbose_name = "kimenő jogosultság előzmény"
        verbose_name_plural = "kimenő jogosultság előzmények"

    def __str__(self):
        return f"{self.student} -> {self.new_eligibility} @ {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def previous_label(self):
        return PassEligibility(self.previous_eligibility).label if self.previous_eligibility else "—"

    @property
    def new_label(self):
        return PassEligibility(self.new_eligibility).label if self.new_eligibility else "—"

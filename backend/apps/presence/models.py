from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class PresenceSource(models.TextChoices):
    """Where a status change came from (spec section 14)."""

    STUDENT = "student", "Diák"
    TEACHER = "teacher", "Nevelőtanár"
    PORTER = "porter", "Portás"
    ADMIN = "admin", "Adminisztrátor"
    EVENING_CHECK = "evening_check", "Esti ellenőrzés"
    # Retired source: the morning round now records as ROOM_CHECK. Kept so
    # events written before the September 2026 rework still render.
    MORNING_CHECK = "morning_check", "Reggeli ellenőrzés (régi)"
    ROOM_CHECK = "room_check", "Szobaellenőrzés"
    WEEKEND_CHECK = "weekend_check", "Hétvégi ellenőrzés"
    IMPORT = "import", "Importálás"
    SYSTEM = "system", "Rendszer"


class StatusTypeQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def student_selectable(self):
        return self.active().filter(student_selectable=True)


class StatusType(TimeStampedModel):
    """A configurable presence status.

    Terminology is data, not code: labels are editable and new statuses can be
    added without a deploy (spec section 11). Behaviour hangs off
    ``counts_as_inside``, never off the label.
    """

    INSIDE = "inside"
    OUTSIDE = "outside"
    SCHOOL = "school"
    DOCTOR = "doctor"
    NIGHT_LEAVE = "night_leave"
    HOME = "home"
    OTHER = "other"

    code = models.SlugField(max_length=32, unique=True)
    label = models.CharField(max_length=64)
    short_label = models.CharField(max_length=16, blank=True)
    color = models.CharField(max_length=16, default="#64748b")
    counts_as_inside = models.BooleanField(default=False)
    student_selectable = models.BooleanField(default=True)
    requires_note = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    objects = StatusTypeQuerySet.as_manager()

    class Meta:
        ordering = ["sort_order", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=models.Q(is_default=True),
                name="statustype_single_default",
            )
        ]

    def __str__(self):
        return self.label

    @classmethod
    def default(cls):
        return cls.objects.filter(is_default=True).first() or cls.objects.active().first()

    @classmethod
    def by_code(cls, code):
        return cls.objects.filter(code=code).first()


class StudentPresence(TimeStampedModel):
    """Current presence state - the answer to "where is this student now?".

    History lives in :class:`PresenceEvent`; this row is overwritten freely.
    """

    student = models.OneToOneField(
        "students.StudentProfile", on_delete=models.CASCADE, related_name="presence"
    )
    status = models.ForeignKey(StatusType, on_delete=models.PROTECT, related_name="+")
    note = models.CharField(max_length=255, blank=True)
    source = models.CharField(
        max_length=32, choices=PresenceSource.choices, default=PresenceSource.SYSTEM
    )
    changed_at = models.DateTimeField(default=timezone.now)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="presence_changes",
    )
    expected_return_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["student__full_name"]
        indexes = [models.Index(fields=["status", "changed_at"])]

    def __str__(self):
        return f"{self.student}: {self.status}"

    @property
    def is_inside(self):
        return self.status.counts_as_inside


class PresenceEvent(models.Model):
    """Immutable record of one status change (spec section 13).

    Never updated, never deleted during normal operation.
    """

    student = models.ForeignKey(
        "students.StudentProfile", on_delete=models.PROTECT, related_name="presence_events"
    )
    old_status = models.ForeignKey(
        StatusType, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    new_status = models.ForeignKey(StatusType, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=32, choices=PresenceSource.choices)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_presence_events",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["student", "-created_at"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        old = self.old_status.label if self.old_status else "-"
        return f"{self.student}: {old} -> {self.new_status.label}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("PresenceEvent rows are immutable.")
        super().save(*args, **kwargs)

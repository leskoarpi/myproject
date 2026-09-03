from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class PermissionStatus(models.TextChoices):
    EMPTY = "empty", "Nincs engedély"
    ACTIVE = "active", "Érvényes"
    EXPIRED = "expired", "Lejárt"
    REVOKED = "revoked", "Visszavonva"


class PermissionType(models.TextChoices):
    STANDING = "standing", "Állandó kimenő"
    DAILY = "daily", "Napi kimenő"
    NIGHT = "night", "Éjszakai kimenő"
    CUSTOM = "custom", "Egyedi"


class HistoryAction(models.TextChoices):
    GRANT = "grant", "Kiadás"
    UPDATE = "update", "Módosítás"
    REVOKE = "revoke", "Visszavonás"
    EXPIRE = "expire", "Lejárat"


class LeavePermission(TimeStampedModel):
    """The student's *current* leave permission.

    One row per student, overwritten in place; every change also appends to
    :class:`LeavePermissionHistory` (spec sections 38-39).
    """

    student = models.OneToOneField(
        "students.StudentProfile", on_delete=models.CASCADE, related_name="leave_permission"
    )
    status = models.CharField(
        max_length=16, choices=PermissionStatus.choices, default=PermissionStatus.EMPTY
    )
    permission_type = models.CharField(
        max_length=16, choices=PermissionType.choices, blank=True
    )
    value = models.CharField(max_length=255, blank=True, help_text="Free-form detail, e.g. times.")
    granted_at = models.DateTimeField(null=True, blank=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["student__full_name"]
        indexes = [models.Index(fields=["status", "expires_at"])]

    def __str__(self):
        return f"{self.student}: {self.get_status_display()}"

    @property
    def is_active(self):
        if self.status != PermissionStatus.ACTIVE:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()

    @property
    def is_expiring_soon(self):
        if not self.is_active or self.expires_at is None:
            return False
        return (self.expires_at - timezone.now()).total_seconds() < 24 * 3600


class LeavePermissionHistory(models.Model):
    """Append-only trail of permission changes. Never rewritten."""

    student = models.ForeignKey(
        "students.StudentProfile",
        on_delete=models.PROTECT,
        related_name="leave_permission_history",
    )
    action = models.CharField(max_length=16, choices=HistoryAction.choices)
    previous_status = models.CharField(max_length=16, blank=True)
    previous_value = models.CharField(max_length=255, blank=True)
    new_status = models.CharField(max_length=16, blank=True)
    new_value = models.CharField(max_length=255, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["student", "-created_at"])]
        verbose_name_plural = "leave permission history"

    def __str__(self):
        return f"{self.student} {self.action} @ {self.created_at:%Y-%m-%d %H:%M}"

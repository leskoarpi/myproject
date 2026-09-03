from django.conf import settings
from django.db import models
from django.utils import timezone


class AuditAction(models.TextChoices):
    CREATE = "create", "Létrehozás"
    UPDATE = "update", "Módosítás"
    ARCHIVE = "archive", "Archiválás"
    DELETE = "delete", "Törlés"
    LOGIN = "login", "Bejelentkezés"
    LOGIN_FAILED = "login_failed", "Sikertelen bejelentkezés"
    LOGOUT = "logout", "Kijelentkezés"
    PASSWORD_CHANGE = "password_change", "Jelszóváltás"
    CLOSE = "close", "Lezárás"
    REOPEN = "reopen", "Újranyitás"
    APPROVE = "approve", "Jóváhagyás"
    REJECT = "reject", "Elutasítás"
    IMPORT = "import", "Importálás"
    EXPORT = "export", "Exportálás"
    MAINTENANCE = "maintenance", "Karbantartás"


class AuditLog(models.Model):
    """Who did what to which record.

    Deliberately separate from domain history (PresenceEvent,
    LeavePermissionHistory, ...) - see spec section 46. Values are redacted
    before they land here; see :mod:`apps.audit.services`.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=32, choices=AuditAction.choices)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    target_repr = models.CharField(max_length=255, blank=True)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    note = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["action"]),
        ]

    def __str__(self):
        who = self.username or "system"
        return f"{who} {self.action} {self.target_type}#{self.target_id}"

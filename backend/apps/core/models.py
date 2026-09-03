from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    """Base class for every mutable domain record."""

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SchoolYearQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)


class SchoolYear(TimeStampedModel):
    """A dormitory school year, e.g. "2025/2026"."""

    name = models.CharField(max_length=32, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=False)

    objects = SchoolYearQuerySet.as_manager()

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gt=models.F("start_date")),
                name="schoolyear_end_after_start",
            ),
            # At most one active school year, enforced by the database.
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="schoolyear_single_active",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": "End date must be after the start date."})

    def contains(self, day):
        return self.start_date <= day <= self.end_date

    @classmethod
    def current(cls):
        return cls.objects.active().first()


class ModuleKey(models.TextChoices):
    EVENING_CHECK = "evening_check", "Esti ellenőrzés"
    MORNING_CHECK = "morning_check", "Reggeli ellenőrzés"
    ROOM_CHECKS = "room_checks", "Szobaellenőrzés"
    WEEKEND_STAY = "weekend_stay", "Hétvégi bennmaradás"
    LEAVE_PERMISSIONS = "leave_permissions", "Kimenő engedélyek"


class SystemModule(TimeStampedModel):
    """A switchable feature area.

    Disabling a module hides its navigation *and* blocks its views; existing
    historical data is never touched.
    """

    key = models.CharField(max_length=64, choices=ModuleKey.choices, unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({'on' if self.is_enabled else 'off'})"

    @classmethod
    def enabled_keys(cls):
        return set(cls.objects.filter(is_enabled=True).values_list("key", flat=True))

    @classmethod
    def is_module_enabled(cls, key):
        module = cls.objects.filter(key=key).first()
        # An unknown/not-yet-seeded module is treated as enabled so a fresh
        # install is usable; seeding happens in the seed_reference_data command.
        return True if module is None else module.is_enabled


class SystemSetting(TimeStampedModel):
    """Small operator-editable settings kept out of the environment."""

    key = models.CharField(max_length=64, unique=True)
    value = models.JSONField(default=dict, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.key

    @classmethod
    def get(cls, key, default=None):
        setting = cls.objects.filter(key=key).first()
        return default if setting is None else setting.value

    @classmethod
    def set(cls, key, value, description=""):
        obj, _ = cls.objects.update_or_create(
            key=key, defaults={"value": value, "description": description}
        )
        return obj

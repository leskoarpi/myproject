from django.db import models

from apps.core.models import TimeStampedModel

WEEKDAY_NAMES = [
    "hétfő",
    "kedd",
    "szerda",
    "csütörtök",
    "péntek",
    "szombat",
    "vasárnap",
]


class DayStatus(models.TextChoices):
    NORMAL = "normal", "Tanítási nap"
    WEEKEND = "weekend", "Hétvége"
    HOLIDAY = "holiday", "Ünnepnap"
    BREAK = "break", "Szünet"
    CLOSED = "closed", "Kollégium zárva"


class CalendarDayQuerySet(models.QuerySet):
    def requiring_evening_check(self):
        return self.filter(evening_check_required=True)

    def between(self, start, end):
        return self.filter(date__gte=start, date__lte=end)


class CalendarDay(TimeStampedModel):
    """One dormitory day.

    Only ``date`` is stored; weekday, month and weekday name are derived
    (spec sections 15 and 54).
    """

    date = models.DateField("dátum", unique=True)
    school_year = models.ForeignKey(
        "core.SchoolYear", on_delete=models.CASCADE, related_name="calendar_days"
    )
    status = models.CharField(
        "nap típusa", max_length=16, choices=DayStatus.choices, default=DayStatus.NORMAL
    )
    evening_check_required = models.BooleanField("esti ellenőrzés kell", default=True)
    note = models.CharField("megjegyzés", max_length=255, blank=True)

    objects = CalendarDayQuerySet.as_manager()

    class Meta:
        verbose_name = "naptári nap"
        verbose_name_plural = "naptári napok"
        ordering = ["-date"]
        indexes = [models.Index(fields=["-date"]), models.Index(fields=["school_year", "date"])]

    def __str__(self):
        return f"{self.date:%Y-%m-%d} ({self.weekday_name})"

    @property
    def weekday(self):
        """Monday = 0."""
        return self.date.weekday()

    @property
    def weekday_name(self):
        return WEEKDAY_NAMES[self.weekday]

    @property
    def is_weekend(self):
        return self.weekday >= 5

    @property
    def year(self):
        return self.date.year

    @property
    def month(self):
        return self.date.month

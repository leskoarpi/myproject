import datetime as dt

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel
from apps.inspections.models import InspectionSession, InspectionState  # noqa: F401


def friday_of(day):
    """The Friday that starts the weekend containing ``day``.

    Sat/Sun belong to the Friday just gone; Mon-Thu look forward to the coming
    Friday, which is what "register for the weekend" means mid-week.
    """
    weekday = day.weekday()  # Mon=0 ... Sun=6
    if weekday <= 3:  # Mon-Thu -> upcoming Friday
        return day + dt.timedelta(days=4 - weekday)
    return day - dt.timedelta(days=weekday - 4)  # Fri/Sat/Sun -> this Friday


class StayStatus(models.TextChoices):
    PENDING = "pending", "Elbírálásra vár"
    APPROVED = "approved", "Jóváhagyva"
    REJECTED = "rejected", "Elutasítva"
    CANCELLED = "cancelled", "Visszavonva"


class WeekendStayQuerySet(models.QuerySet):
    def for_weekend(self, friday):
        return self.filter(weekend_start=friday)

    def approved(self):
        return self.filter(status=StayStatus.APPROVED)

    def pending(self):
        return self.filter(status=StayStatus.PENDING)


class WeekendStay(TimeStampedModel):
    """A request to stay in the dormitory over one weekend.

    Two flavours share this table: a resident student's own request, and a
    management-created *guest* entry. A guest never consumes a weekday room
    allocation - no RoomAssignment is created for them (spec section 31).
    """

    weekend_start = models.DateField(help_text="The Friday the weekend starts on.")
    student = models.ForeignKey(
        "students.StudentProfile",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="weekend_stays",
    )
    is_guest = models.BooleanField(default=False)

    # Guest details - only used when is_guest is True.
    guest_name = models.CharField(max_length=200, blank=True)
    guest_phone = models.CharField(max_length=32, blank=True)
    guest_email = models.EmailField(blank=True)
    guest_guardian_name = models.CharField(max_length=200, blank=True)
    guest_guardian_phone = models.CharField(max_length=32, blank=True)
    guest_guardian_email = models.EmailField(blank=True)

    room = models.ForeignKey(
        "rooms.Room", null=True, blank=True, on_delete=models.SET_NULL, related_name="weekend_stays"
    )
    # Frozen at request time so the printable roster stays truthful.
    room_number = models.CharField(max_length=16, blank=True)
    group_name = models.CharField(max_length=128, blank=True)
    display_name = models.CharField(max_length=200, blank=True)

    friday_stay = models.BooleanField(default=False)
    saturday_stay = models.BooleanField(default=False)
    note = models.TextField(blank=True)

    status = models.CharField(
        max_length=16, choices=StayStatus.choices, default=StayStatus.PENDING
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)

    objects = WeekendStayQuerySet.as_manager()

    class Meta:
        ordering = ["room_number", "display_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "weekend_start"],
                condition=models.Q(student__isnull=False),
                name="weekendstay_one_per_student_per_weekend",
            ),
            models.CheckConstraint(
                condition=models.Q(is_guest=True, student__isnull=True)
                | models.Q(is_guest=False, student__isnull=False),
                name="weekendstay_guest_xor_student",
            ),
            models.CheckConstraint(
                condition=models.Q(friday_stay=True) | models.Q(saturday_stay=True),
                name="weekendstay_at_least_one_night",
            ),
        ]
        indexes = [models.Index(fields=["weekend_start", "status"])]

    def __str__(self):
        return f"{self.display_name or self.student} - {self.weekend_start:%Y-%m-%d}"

    def clean(self):
        if self.is_guest and not self.guest_name:
            raise ValidationError({"guest_name": "A guest entry needs a name."})
        if not self.friday_stay and not self.saturday_stay:
            raise ValidationError("Select at least one night.")
        if self.weekend_start and self.weekend_start.weekday() != 4:
            raise ValidationError({"weekend_start": "The weekend must start on a Friday."})

    def save(self, *args, **kwargs):
        if not self.display_name:
            self.display_name = self.guest_name if self.is_guest else str(self.student)
        if self.room_id and not self.room_number:
            self.room_number = self.room.number
        if not self.group_name and self.student_id and self.student.group:
            self.group_name = self.student.group.name
        super().save(*args, **kwargs)

    @property
    def is_pending(self):
        return self.status == StayStatus.PENDING

    @property
    def saturday_date(self):
        return self.weekend_start + dt.timedelta(days=1)

    @property
    def sunday_date(self):
        return self.weekend_start + dt.timedelta(days=2)


class WeekendCheckType(models.TextChoices):
    """Weekends are checked at night only.

    The morning rounds the old system ran (Saturday and Sunday morning) were
    dropped: what matters over a weekend is who actually slept in the building.
    """

    FRIDAY_NIGHT = "friday_evening", "Péntek éjszaka"
    SATURDAY_NIGHT = "saturday_evening", "Szombat éjszaka"


# Which day of the weekend each check happens on, as an offset from Friday.
CHECK_DAY_OFFSET = {
    WeekendCheckType.FRIDAY_NIGHT: 0,
    WeekendCheckType.SATURDAY_NIGHT: 1,
}

# Which stay night a check is relevant to.
CHECK_REQUIRES_NIGHT = {
    WeekendCheckType.FRIDAY_NIGHT: "friday_stay",
    WeekendCheckType.SATURDAY_NIGHT: "saturday_stay",
}


class WeekendCheckResult(models.TextChoices):
    INSIDE = "inside", "Bent"
    OUTSIDE = "outside", "Kint"
    PENDING = "pending", "Nem ellenőrzött"


class WeekendCheckSession(InspectionSession):
    weekend_start = models.DateField()
    check_type = models.CharField(max_length=32, choices=WeekendCheckType.choices)

    class Meta:
        ordering = ["-weekend_start", "check_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["weekend_start", "check_type"], name="weekendsession_unique_weekend_type"
            )
        ]

    def __str__(self):
        return f"{self.get_check_type_display()} - {self.weekend_start:%Y-%m-%d}"

    @property
    def check_date(self):
        return self.weekend_start + dt.timedelta(days=CHECK_DAY_OFFSET[self.check_type])


class WeekendCheck(TimeStampedModel):
    session = models.ForeignKey(
        WeekendCheckSession, on_delete=models.CASCADE, related_name="checks"
    )
    stay = models.ForeignKey(WeekendStay, on_delete=models.CASCADE, related_name="checks")
    result = models.CharField(
        max_length=16, choices=WeekendCheckResult.choices, default=WeekendCheckResult.PENDING
    )
    note = models.CharField(max_length=255, blank=True)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["stay__room_number", "stay__display_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "stay"], name="weekendcheck_unique_session_stay"
            )
        ]

    def __str__(self):
        return f"{self.stay} - {self.get_result_display()}"

    def mark(self, result, actor, note=""):
        self.result = result
        self.note = note[:255]
        self.checked_by = actor
        self.checked_at = timezone.now()

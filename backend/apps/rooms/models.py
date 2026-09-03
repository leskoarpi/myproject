from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


def infer_floor(room_number):
    """Convenience only: derive a floor from the leading digits of a number.

    The legacy system treated this as authoritative; here ``Room.floor`` is
    explicit data and this only pre-fills it (spec section 8).
    """
    digits = "".join(ch for ch in str(room_number) if ch.isdigit())
    if not digits:
        return None
    if len(digits) <= 2:
        return int(digits[0])
    return int(digits[:-2])


class RoomQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def on_floor(self, floor):
        return self.filter(floor=floor)


class Room(TimeStampedModel):
    number = models.CharField("szobaszám", max_length=16, unique=True)
    floor = models.PositiveSmallIntegerField("emelet")
    capacity = models.PositiveSmallIntegerField("férőhely", validators=[MinValueValidator(1)])
    notes = models.TextField("megjegyzés", blank=True)
    is_active = models.BooleanField("aktív", default=True)

    objects = RoomQuerySet.as_manager()

    class Meta:
        verbose_name = "szoba"
        verbose_name_plural = "szobák"
        ordering = ["floor", "number"]
        indexes = [models.Index(fields=["floor"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(capacity__gte=1), name="room_capacity_positive"
            )
        ]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if self.floor is None:
            inferred = infer_floor(self.number)
            if inferred is not None:
                self.floor = inferred
        super().save(*args, **kwargs)

    @property
    def occupancy(self):
        return self.assignments.filter(is_active=True).count()

    @property
    def free_places(self):
        return max(self.capacity - self.occupancy, 0)


class RoomAssignmentQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def for_school_year(self, school_year):
        return self.filter(school_year=school_year)


class RoomAssignment(TimeStampedModel):
    """Historical record of a student living in a room.

    The active assignment - not a ``current_room`` column on the student - is
    the single source of truth for where a student lives (spec section 9).
    """

    student = models.ForeignKey(
        "students.StudentProfile", on_delete=models.PROTECT, related_name="room_assignments"
    )
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="assignments")
    school_year = models.ForeignKey(
        "core.SchoolYear", on_delete=models.PROTECT, related_name="room_assignments"
    )
    start_date = models.DateField("kezdet")
    end_date = models.DateField("vége", null=True, blank=True)
    is_active = models.BooleanField("aktív", default=True)
    note = models.CharField("megjegyzés", max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_room_assignments",
    )
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ended_room_assignments",
    )

    objects = RoomAssignmentQuerySet.as_manager()

    class Meta:
        verbose_name = "szobabeosztás"
        verbose_name_plural = "szobabeosztások"
        ordering = ["-start_date", "-id"]
        indexes = [
            models.Index(fields=["student", "is_active"]),
            models.Index(fields=["room", "is_active"]),
        ]
        constraints = [
            # A student lives in exactly one room at a time.
            models.UniqueConstraint(
                fields=["student"],
                condition=models.Q(is_active=True),
                name="roomassignment_one_active_per_student",
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__isnull=True)
                | models.Q(end_date__gte=models.F("start_date")),
                name="roomassignment_end_after_start",
            ),
            models.CheckConstraint(
                condition=models.Q(is_active=False) | models.Q(end_date__isnull=True),
                name="roomassignment_active_has_no_end_date",
            ),
        ]

    def __str__(self):
        return f"{self.student} -> {self.room} ({self.school_year})"

    def clean(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "A vége dátum nem lehet korábbi a kezdetnél."})
        if self.school_year_id and not self.school_year.contains(self.start_date):
            raise ValidationError(
                {"start_date": "A kezdő dátum a kiválasztott tanéven kívül esik."}
            )

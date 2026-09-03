from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class InspectionState(models.TextChoices):
    OPEN = "open", "Nyitva"
    CLOSED = "closed", "Lezárva"


class InspectionSession(TimeStampedModel):
    """Shared lifecycle for every kind of inspection session.

    Transitions are OPEN -> CLOSED -> OPEN (reopen), and are performed only by
    the service layer (spec section 56). The ``locked_*`` columns record who is
    currently editing; the actual concurrency guarantee comes from
    ``select_for_update`` in the services, not from these timestamps.
    """

    state = models.CharField(
        max_length=16, choices=InspectionState.choices, default=InspectionState.OPEN
    )
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    opened_at = models.DateTimeField(default=timezone.now)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopen_reason = models.CharField(max_length=255, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def is_open(self):
        return self.state == InspectionState.OPEN

    @property
    def is_closed(self):
        return self.state == InspectionState.CLOSED

    def lock_is_stale(self, timeout_seconds):
        if self.locked_at is None:
            return True
        return (timezone.now() - self.locked_at).total_seconds() > timeout_seconds

    def lock_holder_display(self):
        return self.locked_by.display_name if self.locked_by_id else ""


# --------------------------------------------------------------------------
# Evening presence inspection (spec sections 16-18)
# --------------------------------------------------------------------------


class EveningCheckSession(InspectionSession):
    calendar_day = models.ForeignKey(
        "dormcalendar.CalendarDay", on_delete=models.CASCADE, related_name="evening_sessions"
    )
    floor = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["-calendar_day__date", "floor"]
        constraints = [
            models.UniqueConstraint(
                fields=["calendar_day", "floor"], name="eveningsession_unique_day_floor"
            )
        ]
        indexes = [models.Index(fields=["state"])]

    def __str__(self):
        return f"Esti ellenőrzés {self.calendar_day.date:%Y-%m-%d} / {self.floor}. emelet"

    @property
    def date(self):
        return self.calendar_day.date


class EveningCheckResult(TimeStampedModel):
    session = models.ForeignKey(
        EveningCheckSession, on_delete=models.CASCADE, related_name="results"
    )
    student = models.ForeignKey(
        "students.StudentProfile", on_delete=models.PROTECT, related_name="evening_results"
    )
    status = models.ForeignKey("presence.StatusType", on_delete=models.PROTECT, related_name="+")
    # Snapshot so a later room move does not rewrite this evening's sheet.
    room_number = models.CharField(max_length=16, blank=True)
    note = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["room_number", "student__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "student"], name="eveningresult_unique_session_student"
            )
        ]

    def __str__(self):
        return f"{self.student} - {self.status}"


# --------------------------------------------------------------------------
# Morning inspection (spec sections 19-24)
# --------------------------------------------------------------------------


class MorningResult(models.TextChoices):
    PRESENT = "present", "Bent aludt"
    ABSENT = "absent", "Nem volt bent"
    LEFT_OVERNIGHT = "left_overnight", "Éjszaka elment"
    RETURNED_LATE = "returned_late", "Éjszaka érkezett"
    UNKNOWN = "unknown", "Nem ellenőrzött"


class MonthState(models.TextChoices):
    OPEN = "open", "Nyitva"
    CLOSED = "closed", "Lezárva"


class MorningMonth(TimeStampedModel):
    """Monthly grouping of morning snapshots (spec section 24)."""

    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    state = models.CharField(max_length=16, choices=MonthState.choices, default=MonthState.OPEN)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(fields=["year", "month"], name="morningmonth_unique_period"),
            models.CheckConstraint(
                condition=models.Q(month__gte=1) & models.Q(month__lte=12),
                name="morningmonth_valid_month",
            ),
        ]

    def __str__(self):
        return f"{self.year}-{self.month:02d}"

    @classmethod
    def for_date(cls, day):
        obj, _ = cls.objects.get_or_create(year=day.year, month=day.month)
        return obj


class MorningSnapshot(InspectionSession):
    """An immutable historical baseline for one morning.

    Item rows carry copies of the student's name, room, floor and class so the
    record stays truthful even after the student moves or is renamed
    (spec sections 19-20, 71).
    """

    calendar_day = models.OneToOneField(
        "dormcalendar.CalendarDay", on_delete=models.CASCADE, related_name="morning_snapshot"
    )
    month = models.ForeignKey(
        MorningMonth, on_delete=models.PROTECT, related_name="snapshots"
    )
    # The moment the student's state was evaluated against (default 04:00).
    cutoff_at = models.DateTimeField()
    generated_at = models.DateTimeField(default=timezone.now)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-calendar_day__date"]

    def __str__(self):
        return f"Reggeli pillanatkép {self.calendar_day.date:%Y-%m-%d}"

    @property
    def date(self):
        return self.calendar_day.date


class MorningSnapshotItem(TimeStampedModel):
    snapshot = models.ForeignKey(
        MorningSnapshot, on_delete=models.CASCADE, related_name="items"
    )
    student = models.ForeignKey(
        "students.StudentProfile", on_delete=models.PROTECT, related_name="morning_items"
    )

    # --- frozen historical values -------------------------------------
    student_name = models.CharField(max_length=200)
    room_number = models.CharField(max_length=16, blank=True)
    floor = models.PositiveSmallIntegerField(null=True, blank=True)
    school_class = models.CharField(max_length=32, blank=True)
    group_name = models.CharField(max_length=128, blank=True)
    evening_status_code = models.CharField(max_length=32, blank=True)
    evening_status_label = models.CharField(max_length=64, blank=True)
    cutoff_status_code = models.CharField(max_length=32, blank=True)
    cutoff_status_label = models.CharField(max_length=64, blank=True)

    # --- results ------------------------------------------------------
    calculated_result = models.CharField(
        max_length=16, choices=MorningResult.choices, default=MorningResult.UNKNOWN
    )
    final_result = models.CharField(
        max_length=16, choices=MorningResult.choices, default=MorningResult.UNKNOWN
    )
    is_reviewed = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["floor", "room_number", "student_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "student"], name="morningitem_unique_snapshot_student"
            )
        ]
        indexes = [models.Index(fields=["snapshot", "final_result"])]

    def __str__(self):
        return f"{self.student_name} - {self.get_final_result_display()}"

    @property
    def was_corrected(self):
        return self.final_result != self.calculated_result


# --------------------------------------------------------------------------
# Room / cleanliness inspection (spec sections 25-28)
# --------------------------------------------------------------------------


class RoomCheckSession(InspectionSession):
    date = models.DateField()
    floor = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["-date", "floor"]
        constraints = [
            models.UniqueConstraint(fields=["date", "floor"], name="roomchecksession_unique_day_floor")
        ]

    def __str__(self):
        return f"Szobaellenőrzés {self.date:%Y-%m-%d} / {self.floor}. emelet"


class RoomCheck(TimeStampedModel):
    """Current condition record for one room in one session.

    Edits do not overwrite silently: every save first copies the previous
    values into :class:`RoomCheckHistory` (spec section 28).
    """

    RATING_MIN = 1
    RATING_MAX = 5

    session = models.ForeignKey(RoomCheckSession, on_delete=models.CASCADE, related_name="checks")
    room = models.ForeignKey("rooms.Room", on_delete=models.PROTECT, related_name="checks")
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    problems = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["room__number"]
        constraints = [
            models.UniqueConstraint(fields=["session", "room"], name="roomcheck_unique_session_room"),
            models.CheckConstraint(
                condition=models.Q(rating__isnull=True)
                | (models.Q(rating__gte=1) & models.Q(rating__lte=5)),
                name="roomcheck_rating_range",
            ),
        ]

    def __str__(self):
        return f"{self.room} - {self.rating if self.rating is not None else '-'}"

    @property
    def is_recorded(self):
        return self.checked_at is not None


class RoomCheckHistory(models.Model):
    """Append-only previous versions of a :class:`RoomCheck`."""

    room_check = models.ForeignKey(
        RoomCheck, on_delete=models.CASCADE, related_name="history_entries"
    )
    previous_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    previous_problems = models.TextField(blank=True)
    previous_notes = models.TextField(blank=True)
    saved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    saved_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-saved_at"]
        verbose_name_plural = "room check history"

    def __str__(self):
        return f"{self.room_check_id} @ {self.saved_at:%Y-%m-%d %H:%M}"


class RoomCheckPresence(models.TextChoices):
    PRESENT = "present", "Jelen"
    ABSENT = "absent", "Nincs jelen"
    UNKNOWN = "unknown", "Nem ellenőrzött"


class RoomCheckStudentResult(TimeStampedModel):
    """Per-student detail captured during a room inspection (spec section 27)."""

    room_check = models.ForeignKey(
        RoomCheck, on_delete=models.CASCADE, related_name="student_results"
    )
    student = models.ForeignKey(
        "students.StudentProfile", on_delete=models.PROTECT, related_name="room_check_results"
    )
    presence_result = models.CharField(
        max_length=16, choices=RoomCheckPresence.choices, default=RoomCheckPresence.UNKNOWN
    )
    detail_code = models.CharField(max_length=32, blank=True)
    departure_time = models.TimeField(null=True, blank=True)
    detail_note = models.CharField(max_length=255, blank=True)
    saved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    saved_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["student__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["room_check", "student"], name="roomcheckstudent_unique_check_student"
            )
        ]

    def __str__(self):
        return f"{self.student} - {self.get_presence_result_display()}"

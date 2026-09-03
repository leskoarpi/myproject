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
# Morning round: room condition + each resident's morning status
# (spec sections 25-28, merged with the former morning check)
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


class RoomCheckStudentResult(TimeStampedModel):
    """One resident's morning status, recorded during the room round.

    This replaces the separate morning inspection: the teacher walks the floor
    once, rates the room and sets each student's status in the same pass.
    Saving a result also moves the student's live presence, sourced as
    ``room_check`` so the history stays attributable.

    The name/room/class columns are frozen copies taken at entry time, so a
    later room move or rename cannot rewrite this morning's sheet
    (spec section 71).
    """

    room_check = models.ForeignKey(
        RoomCheck, on_delete=models.CASCADE, related_name="student_results"
    )
    student = models.ForeignKey(
        "students.StudentProfile", on_delete=models.PROTECT, related_name="room_check_results"
    )
    status = models.ForeignKey(
        "presence.StatusType",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    # --- frozen historical values -------------------------------------
    student_name = models.CharField(max_length=200, blank=True)
    room_number = models.CharField(max_length=16, blank=True)
    school_class = models.CharField(max_length=32, blank=True)

    note = models.CharField(max_length=255, blank=True)
    saved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    saved_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["room_number", "student_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["room_check", "student"], name="roomcheckstudent_unique_check_student"
            )
        ]
        indexes = [models.Index(fields=["student", "-saved_at"])]

    def __str__(self):
        label = self.status.label if self.status_id else "—"
        return f"{self.student_name or self.student}: {label}"

    @property
    def is_recorded(self):
        return self.status_id is not None

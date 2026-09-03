from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class StudentQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def archived(self):
        return self.filter(is_active=False)

    def in_groups(self, group_ids):
        return self.filter(group_id__in=list(group_ids))

    def with_current_room(self):
        return self.prefetch_related("room_assignments__room")


class StudentProfile(TimeStampedModel):
    """Dormitory resident.

    Separated from the authentication identity on purpose: the ``User`` row
    carries login state only (spec section 6).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="student_profile",
    )
    full_name = models.CharField(max_length=200)
    education_id = models.CharField(
        "oktatási azonosító", max_length=32, unique=True, null=True, blank=True
    )
    birth_date = models.DateField(null=True, blank=True)
    school_name = models.CharField(max_length=200, blank=True)
    school_class = models.CharField(max_length=32, blank=True)
    group = models.ForeignKey(
        "people.Group",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="students",
    )

    phone = models.CharField(max_length=32, blank=True)
    address = models.CharField(max_length=255, blank=True)

    # Sensitive personal data - see SENSITIVE_FIELDS below.
    guardian_name = models.CharField(max_length=200, blank=True)
    guardian_phone = models.CharField(max_length=32, blank=True)
    guardian_email = models.EmailField(blank=True)
    emergency_contact_name = models.CharField(max_length=200, blank=True)
    emergency_contact_phone = models.CharField(max_length=32, blank=True)
    medical_notes = models.TextField(blank=True)
    staff_notes = models.TextField(blank=True)

    move_in_date = models.DateField(null=True, blank=True)
    move_out_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="archived_students",
    )

    objects = StudentQuerySet.as_manager()

    # Fields a teacher may edit directly; everything else goes through a
    # StudentChangeRequest (spec section 37).
    TEACHER_EDITABLE_FIELDS = ("phone", "staff_notes", "school_class")

    # Fields that need VIEW_SENSITIVE_STUDENT_DATA to be rendered.
    SENSITIVE_FIELDS = (
        "medical_notes",
        "guardian_name",
        "guardian_phone",
        "guardian_email",
        "emergency_contact_name",
        "emergency_contact_phone",
        "address",
    )

    class Meta:
        ordering = ["full_name"]
        indexes = [
            models.Index(fields=["is_active"]),
            models.Index(fields=["group", "is_active"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(move_out_date__isnull=True)
                | models.Q(move_in_date__isnull=True)
                | models.Q(move_out_date__gte=models.F("move_in_date")),
                name="student_move_out_after_move_in",
            )
        ]

    def __str__(self):
        return self.full_name

    def clean(self):
        if self.move_in_date and self.move_out_date and self.move_out_date < self.move_in_date:
            raise ValidationError({"move_out_date": "Move-out cannot precede move-in."})

    @property
    def current_assignment(self):
        return self.room_assignments.filter(is_active=True).select_related("room").first()

    @property
    def current_room(self):
        assignment = self.current_assignment
        return assignment.room if assignment else None

    @property
    def floor(self):
        room = self.current_room
        return room.floor if room else None

    def archive(self, *, by=None, move_out_date=None):
        self.is_active = False
        self.archived_at = timezone.now()
        self.archived_by = by
        if move_out_date:
            self.move_out_date = move_out_date
        self.save(
            update_fields=[
                "is_active",
                "archived_at",
                "archived_by",
                "move_out_date",
                "updated_at",
            ]
        )


class ChangeRequestStatus(models.TextChoices):
    PENDING = "pending", "Elbírálásra vár"
    APPROVED = "approved", "Jóváhagyva"
    REJECTED = "rejected", "Elutasítva"
    WITHDRAWN = "withdrawn", "Visszavonva"


class StudentChangeRequest(TimeStampedModel):
    """A teacher's proposal to change a student record, pending review.

    ``proposed_changes`` is structured JSON ({field: new_value}) rather than an
    opaque serialized blob (spec section 37).
    """

    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="change_requests"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="student_change_requests",
    )
    proposed_changes = models.JSONField(default=dict)
    previous_values = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=ChangeRequestStatus.choices, default=ChangeRequestStatus.PENDING
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_student_change_requests",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self):
        return f"Change request #{self.pk} for {self.student} ({self.status})"

    @property
    def is_pending(self):
        return self.status == ChangeRequestStatus.PENDING

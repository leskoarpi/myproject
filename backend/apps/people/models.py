from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Group(TimeStampedModel):
    """A dormitory group ("csoport"). Students belong to one; teachers to many."""

    code = models.CharField("kód", max_length=32, unique=True)
    name = models.CharField("név", max_length=128)
    description = models.TextField("leírás", blank=True)
    is_active = models.BooleanField("aktív", default=True)

    class Meta:
        verbose_name = "csoport"
        verbose_name_plural = "csoportok"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    @property
    def student_count(self):
        return self.students.filter(is_active=True).count()


class Teacher(TimeStampedModel):
    """Profile for an educator account.

    ``groups`` drives object-level student access: a teacher normally reaches
    only the students of their groups (spec section 3.3).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="teacher_profile"
    )
    full_name = models.CharField("teljes név", max_length=200)
    phone = models.CharField("telefonszám", max_length=32, blank=True)
    primary_group = models.ForeignKey(
        Group,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="primary_teachers",
        verbose_name="elsődleges csoport",
    )
    groups = models.ManyToManyField(
        Group, blank=True, related_name="teachers", verbose_name="további csoportok"
    )

    # Management may widen a single teacher's weekend reach (spec section 3.3).
    can_manage_all_weekend_stays = models.BooleanField(
        "teljes hétvégi jogosultság", default=False
    )
    # Escape hatch for staff who legitimately need every student.
    has_all_student_access = models.BooleanField(
        "minden diákhoz hozzáfér", default=False
    )
    is_active = models.BooleanField("aktív", default=True)

    class Meta:
        verbose_name = "nevelőtanár"
        verbose_name_plural = "nevelőtanárok"
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name

    def accessible_group_ids(self):
        ids = set(self.groups.values_list("id", flat=True))
        if self.primary_group_id:
            ids.add(self.primary_group_id)
        return ids

    def save(self, *args, **kwargs):
        if not self.full_name:
            self.full_name = self.user.display_name
        super().save(*args, **kwargs)

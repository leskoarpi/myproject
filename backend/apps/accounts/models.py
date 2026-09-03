from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.db import models
from django.utils import timezone
from django.utils.functional import cached_property

from .capabilities import Role, user_capabilities


class UserManager(DjangoUserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.ADMIN)
        extra_fields.setdefault("must_change_password", False)
        return super().create_superuser(username, email, password, **extra_fields)

    def staff_roles(self):
        return self.filter(
            role__in=[Role.ADMIN, Role.MANAGEMENT, Role.TEACHER, Role.PORTER]
        )


class User(AbstractUser):
    """Authentication identity only.

    Person-level data lives on the related profile (StudentProfile / Teacher);
    nothing dormitory-specific belongs here. See spec section 6.
    """

    email = models.EmailField("email address", unique=True)
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.STUDENT)
    phone = models.CharField(max_length=32, blank=True)

    # New accounts are created with a temporary password and are pushed through
    # a password change on their first request (spec section 4).
    must_change_password = models.BooleanField(default=False)
    password_changed_at = models.DateTimeField(null=True, blank=True)

    # Per-user deviations from the role default.
    extra_capabilities = models.JSONField(default=list, blank=True)
    revoked_capabilities = models.JSONField(default=list, blank=True)

    objects = UserManager()

    class Meta:
        ordering = ["last_name", "first_name", "username"]
        indexes = [models.Index(fields=["role"])]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        full = self.get_full_name().strip()
        return full or self.username

    @cached_property
    def capabilities(self):
        return user_capabilities(self)

    def has_capability(self, capability):
        return capability in self.capabilities

    def has_any_capability(self, *capabilities):
        return any(c in self.capabilities for c in capabilities)

    def refresh_capabilities(self):
        self.__dict__.pop("capabilities", None)

    @property
    def is_student(self):
        return self.role == Role.STUDENT

    @property
    def is_teacher(self):
        return self.role == Role.TEACHER

    @property
    def is_management(self):
        return self.role in {Role.MANAGEMENT, Role.ADMIN} or self.is_superuser

    def set_password(self, raw_password):
        super().set_password(raw_password)
        if raw_password is not None:
            self.password_changed_at = timezone.now()


class LoginAttempt(models.Model):
    """Recent authentication attempts, used for throttling and audit."""

    username = models.CharField(max_length=150)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    successful = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["username", "created_at"])]

    def __str__(self):
        outcome = "ok" if self.successful else "failed"
        return f"{self.username} {outcome} @ {self.created_at:%Y-%m-%d %H:%M}"

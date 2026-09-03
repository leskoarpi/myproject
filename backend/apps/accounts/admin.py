from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import LoginAttempt, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "role", "is_active", "must_change_password")
    list_filter = ("role", "is_active", "is_superuser")
    fieldsets = DjangoUserAdmin.fieldsets + (
        (
            "Kollégium",
            {
                "fields": (
                    "role",
                    "phone",
                    "must_change_password",
                    "extra_capabilities",
                    "revoked_capabilities",
                )
            },
        ),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Kollégium", {"fields": ("email", "role", "must_change_password")}),
    )


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    list_display = ("username", "ip_address", "successful", "created_at")
    list_filter = ("successful",)
    search_fields = ("username",)

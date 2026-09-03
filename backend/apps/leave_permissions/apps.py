from django.apps import AppConfig


class LeavePermissionsConfig(AppConfig):
    """Pass rules.

    The package is still called ``leave_permissions`` so the app label and its
    migration history stay stable; what it holds is the per-student pass rule.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.leave_permissions"
    verbose_name = "Pass rules"

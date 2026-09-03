import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("deakkoli")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    # Runs after the morning cutoff so the snapshot sees the settled state.
    "generate-morning-snapshot": {
        "task": "apps.inspections.tasks.generate_morning_snapshot_task",
        "schedule": crontab(hour=5, minute=0),
    },
    "expire-leave-permissions": {
        "task": "apps.leave_permissions.tasks.expire_leave_permissions_task",
        "schedule": crontab(hour=0, minute=10),
    },
    "ensure-calendar-days": {
        "task": "apps.dormcalendar.tasks.ensure_calendar_days_task",
        "schedule": crontab(hour=0, minute=5),
    },
    "release-stale-inspection-locks": {
        "task": "apps.inspections.tasks.release_stale_locks_task",
        "schedule": crontab(minute="*/10"),
    },
}

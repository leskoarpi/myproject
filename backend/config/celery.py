import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("deakkoli")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# The morning snapshot and the leave-permission expiry jobs are gone: the
# morning round is now recorded by hand during the room check, and passes are a
# per-student rule rather than something that expires.
app.conf.beat_schedule = {
    "ensure-calendar-days": {
        "task": "apps.dormcalendar.tasks.ensure_calendar_days_task",
        "schedule": crontab(hour=0, minute=5),
    },
    "release-stale-inspection-locks": {
        "task": "apps.inspections.tasks.release_stale_locks_task",
        "schedule": crontab(minute="*/10"),
    },
}

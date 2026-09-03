import logging

from celery import shared_task

from .services import ensure_calendar_days

logger = logging.getLogger(__name__)


@shared_task(name="apps.dormcalendar.tasks.ensure_calendar_days_task")
def ensure_calendar_days_task():
    """Keep the calendar populated for the active school year. Idempotent."""
    created = ensure_calendar_days()
    logger.info("ensure_calendar_days created %s day(s)", created)
    return created

"""Scheduled inspection jobs. All of them are idempotent (spec section 61)."""

import datetime as dt
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import EveningCheckSession, InspectionState, RoomCheckSession
from .services import generate_morning_snapshot

logger = logging.getLogger(__name__)


@shared_task(name="apps.inspections.tasks.generate_morning_snapshot_task", bind=True, max_retries=3)
def generate_morning_snapshot_task(self, morning_date=None):
    """Build today's morning snapshot. Running it twice is a no-op."""
    if isinstance(morning_date, str):
        morning_date = dt.date.fromisoformat(morning_date)
    try:
        snapshot = generate_morning_snapshot(morning_date or timezone.localdate())
    except Exception as exc:  # pragma: no cover - retry path
        logger.exception("Morning snapshot generation failed")
        raise self.retry(exc=exc, countdown=300)
    logger.info("Morning snapshot ready: %s (id=%s)", snapshot.date, snapshot.pk)
    return snapshot.pk


@shared_task(name="apps.inspections.tasks.release_stale_locks_task")
def release_stale_locks_task():
    """Clear advisory edit locks abandoned by a closed browser tab."""
    threshold = timezone.now() - dt.timedelta(seconds=settings.INSPECTION_LOCK_TIMEOUT_SECONDS)
    released = 0
    for model in (EveningCheckSession, RoomCheckSession):
        released += model.objects.filter(
            state=InspectionState.OPEN, locked_at__lt=threshold
        ).update(locked_by=None, locked_at=None)
    if released:
        logger.info("Released %s stale inspection lock(s)", released)
    return released

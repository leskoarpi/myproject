"""Scheduled inspection jobs. All of them are idempotent (spec section 61)."""

import datetime as dt
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import EveningCheckSession, InspectionState, RoomCheckSession

logger = logging.getLogger(__name__)


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

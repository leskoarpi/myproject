import logging

from celery import shared_task

from .services import expire_leave_permissions

logger = logging.getLogger(__name__)


@shared_task(name="apps.leave_permissions.tasks.expire_leave_permissions_task")
def expire_leave_permissions_task():
    """Daily expiry sweep. Idempotent and retry-safe."""
    count = expire_leave_permissions()
    logger.info("expire_leave_permissions expired %s permission(s)", count)
    return count

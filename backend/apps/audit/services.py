"""Central audit recording.

Sensitive values never reach the audit table verbatim; they are replaced with
a marker so the trail still shows *that* a field changed (spec section 47).
"""

import logging

from django.db import models

from .middleware import get_client_ip
from .models import AuditAction, AuditLog

logger = logging.getLogger(__name__)

REDACTED = "[nem naplózva]"

SENSITIVE_FIELDS = frozenset(
    {
        "password",
        "raw_password",
        "new_password",
        "old_password",
        "token",
        "secret",
        "api_key",
        "session_key",
        "medical_notes",
        "guardian_phone",
        "guardian_email",
        "emergency_contact_phone",
        "phone",
        "address",
    }
)


def redact(payload):
    """Return a JSON-safe, redacted copy of ``payload``."""
    if payload is None:
        return None
    if isinstance(payload, dict):
        return {
            key: (REDACTED if key.lower() in SENSITIVE_FIELDS else redact(value))
            for key, value in payload.items()
        }
    if isinstance(payload, (list, tuple)):
        return [redact(item) for item in payload]
    if isinstance(payload, models.Model):
        return f"{payload._meta.label}#{payload.pk}"
    if isinstance(payload, (str, int, float, bool)) or payload is None:
        return payload
    return str(payload)


def record_audit(
    *,
    user=None,
    action,
    target=None,
    target_type="",
    target_id="",
    target_repr="",
    old_value=None,
    new_value=None,
    note="",
    ip_address=None,
):
    """Write one audit entry. Never raises into the caller's transaction path
    for anything other than a genuine database error."""
    if target is not None:
        target_type = target_type or target._meta.label
        target_id = target_id or str(target.pk)
        target_repr = target_repr or str(target)[:255]

    return AuditLog.objects.create(
        user=user if (user is not None and getattr(user, "pk", None)) else None,
        username=getattr(user, "username", "") or "",
        action=action,
        target_type=target_type[:64],
        target_id=str(target_id)[:64],
        target_repr=str(target_repr)[:255],
        old_value=redact(old_value),
        new_value=redact(new_value),
        note=note,
        ip_address=ip_address if ip_address is not None else get_client_ip(),
    )


def model_snapshot(instance, fields):
    """Capture selected fields of ``instance`` for a before/after audit pair."""
    snapshot = {}
    for field in fields:
        value = getattr(instance, field, None)
        if isinstance(value, models.Model):
            value = f"{value._meta.label}#{value.pk}"
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        snapshot[field] = value
    return snapshot


__all__ = ["AuditAction", "record_audit", "model_snapshot", "redact", "REDACTED"]

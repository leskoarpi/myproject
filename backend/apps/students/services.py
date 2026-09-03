"""Student domain services.

All multi-step student workflows live here: transaction-aware,
permission-aware, and callable from views, management commands and tests
alike (spec section 55).
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.capabilities import Capability
from apps.audit.services import AuditAction, model_snapshot, record_audit

from .models import ChangeRequestStatus, StudentChangeRequest, StudentProfile
from .selectors import can_edit_student, can_view_student, editable_fields_for

AUDITED_STUDENT_FIELDS = (
    "full_name",
    "education_id",
    "birth_date",
    "school_name",
    "school_class",
    "group",
    "phone",
    "move_in_date",
    "move_out_date",
    "is_active",
)


def _serialize(value):
    if hasattr(value, "pk"):
        return value.pk
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


@transaction.atomic
def update_student(*, student, changes, actor):
    """Apply ``changes`` to ``student`` after checking scope and field rights."""
    if not can_edit_student(actor, student):
        raise PermissionDenied("Ezt a diákot nem szerkesztheted.")

    allowed = editable_fields_for(actor, student)
    rejected = set(changes) - set(allowed)
    if rejected:
        raise PermissionDenied(
            "You may not edit these fields directly: " + ", ".join(sorted(rejected))
        )

    student = StudentProfile.objects.select_for_update().get(pk=student.pk)
    before = model_snapshot(student, AUDITED_STUDENT_FIELDS)

    for field, value in changes.items():
        setattr(student, field, value)
    student.full_clean(exclude=["user"])
    student.save()

    after = model_snapshot(student, AUDITED_STUDENT_FIELDS)
    record_audit(
        user=actor,
        action=AuditAction.UPDATE,
        target=student,
        old_value=before,
        new_value=after,
    )
    return student


@transaction.atomic
def archive_student(*, student, actor, move_out_date=None, note=""):
    """Archive rather than delete: history must stay valid (spec section 7)."""
    if not actor.has_capability(Capability.ARCHIVE_STUDENTS):
        raise PermissionDenied("Nincs jogosultságod diákot archiválni.")
    if not can_view_student(actor, student):
        raise PermissionDenied("Ehhez a diákhoz nincs hozzáférésed.")

    student = StudentProfile.objects.select_for_update().get(pk=student.pk)
    if not student.is_active:
        return student

    before = model_snapshot(student, AUDITED_STUDENT_FIELDS)

    # End the active room assignment so the place is freed.
    assignment = student.room_assignments.filter(is_active=True).first()
    if assignment:
        from apps.rooms.services import end_room_assignment

        end_room_assignment(
            assignment=assignment, actor=actor, end_date=move_out_date or timezone.localdate()
        )

    student.archive(by=actor, move_out_date=move_out_date or timezone.localdate())

    record_audit(
        user=actor,
        action=AuditAction.ARCHIVE,
        target=student,
        old_value=before,
        new_value=model_snapshot(student, AUDITED_STUDENT_FIELDS),
        note=note,
    )
    return student


@transaction.atomic
def reactivate_student(*, student, actor):
    if not actor.has_capability(Capability.ARCHIVE_STUDENTS):
        raise PermissionDenied("Missing capability.")
    student = StudentProfile.objects.select_for_update().get(pk=student.pk)
    before = model_snapshot(student, AUDITED_STUDENT_FIELDS)
    student.is_active = True
    student.archived_at = None
    student.archived_by = None
    student.move_out_date = None
    student.save(
        update_fields=["is_active", "archived_at", "archived_by", "move_out_date", "updated_at"]
    )
    record_audit(
        user=actor,
        action=AuditAction.UPDATE,
        target=student,
        old_value=before,
        new_value=model_snapshot(student, AUDITED_STUDENT_FIELDS),
        note="reactivated",
    )
    return student


@transaction.atomic
def submit_student_change_request(*, student, actor, proposed_changes, reason=""):
    """Teacher proposes changes to fields they cannot write directly."""
    if not actor.has_capability(Capability.REQUEST_STUDENT_CHANGES):
        raise PermissionDenied("Nincs jogosultságod változtatási kérelmet beadni.")
    if not can_view_student(actor, student):
        raise PermissionDenied("Ehhez a diákhoz nincs hozzáférésed.")
    if not proposed_changes:
        raise ValidationError("Nem javasoltál változtatást.")

    valid_fields = {
        f.name for f in StudentProfile._meta.get_fields() if getattr(f, "editable", False)
    }
    unknown = set(proposed_changes) - valid_fields
    if unknown:
        raise ValidationError("Ismeretlen mezők: " + ", ".join(sorted(unknown)))

    previous = {
        field: _serialize(getattr(student, field, None)) for field in proposed_changes
    }
    normalized = {field: _serialize(value) for field, value in proposed_changes.items()}

    request_obj = StudentChangeRequest.objects.create(
        student=student,
        requested_by=actor,
        proposed_changes=normalized,
        previous_values=previous,
        reason=reason,
    )
    record_audit(
        user=actor,
        action=AuditAction.CREATE,
        target=request_obj,
        new_value={"student": student.pk, "fields": sorted(normalized)},
    )
    return request_obj


@transaction.atomic
def approve_student_change_request(*, change_request, actor, note=""):
    if not actor.has_capability(Capability.REVIEW_STUDENT_CHANGES):
        raise PermissionDenied("Nincs jogosultságod változtatási kérelmet elbírálni.")

    change_request = StudentChangeRequest.objects.select_for_update().get(
        pk=change_request.pk
    )
    if not change_request.is_pending:
        raise ValidationError("Ezt a kérelmet már elbírálták.")

    student = StudentProfile.objects.select_for_update().get(pk=change_request.student_id)
    before = model_snapshot(student, AUDITED_STUDENT_FIELDS)

    for field, value in change_request.proposed_changes.items():
        model_field = StudentProfile._meta.get_field(field)
        if model_field.is_relation:
            setattr(student, f"{field}_id", value)
        else:
            setattr(student, field, value)
    student.full_clean(exclude=["user"])
    student.save()

    change_request.status = ChangeRequestStatus.APPROVED
    change_request.reviewed_by = actor
    change_request.reviewed_at = timezone.now()
    change_request.review_note = note
    change_request.save(
        update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"]
    )

    record_audit(
        user=actor,
        action=AuditAction.APPROVE,
        target=change_request,
        old_value=before,
        new_value=model_snapshot(student, AUDITED_STUDENT_FIELDS),
        note=note,
    )
    return change_request


@transaction.atomic
def reject_student_change_request(*, change_request, actor, note=""):
    if not actor.has_capability(Capability.REVIEW_STUDENT_CHANGES):
        raise PermissionDenied("Nincs jogosultságod változtatási kérelmet elbírálni.")

    change_request = StudentChangeRequest.objects.select_for_update().get(
        pk=change_request.pk
    )
    if not change_request.is_pending:
        raise ValidationError("Ezt a kérelmet már elbírálták.")

    change_request.status = ChangeRequestStatus.REJECTED
    change_request.reviewed_by = actor
    change_request.reviewed_at = timezone.now()
    change_request.review_note = note
    change_request.save(
        update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"]
    )
    record_audit(
        user=actor, action=AuditAction.REJECT, target=change_request, note=note
    )
    return change_request

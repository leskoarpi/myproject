"""Scoped querysets.

Every view that reaches a student record must go through
:func:`student_queryset_for_user`. A capability check answers "may this user
view students?"; only these selectors answer "*which* students?" (spec 58).
"""

from apps.accounts.capabilities import Capability, Role

from .models import StudentProfile


def student_queryset_for_user(user, *, include_archived=False):
    """Return the students ``user`` is allowed to see."""
    base = StudentProfile.objects.select_related("user", "group")
    if not include_archived:
        base = base.active()

    if not user or not user.is_authenticated or not user.is_active:
        return base.none()

    if user.is_superuser or user.role in {Role.ADMIN, Role.MANAGEMENT}:
        return base

    if user.role == Role.STUDENT:
        # A student only ever sees themselves.
        return base.filter(user=user)

    if user.role == Role.TEACHER:
        if not user.has_capability(Capability.VIEW_STUDENTS):
            return base.none()
        teacher = getattr(user, "teacher_profile", None)
        if teacher is None or not teacher.is_active:
            return base.none()
        if teacher.has_all_student_access:
            return base
        return base.filter(group_id__in=teacher.accessible_group_ids())

    if user.role == Role.PORTER:
        # Porters see the roster for presence purposes but nothing sensitive;
        # the templates gate the sensitive fields separately.
        return base if user.has_capability(Capability.VIEW_STUDENTS) else base.none()

    return base.none()


def presence_queryset_for_user(user, *, include_archived=False):
    """Students whose *presence* ``user`` may see and act on.

    Deliberately wider than :func:`student_queryset_for_user`: presence is a
    whole-building concern. Any staff member on duty answers for every floor,
    so teachers, porters and management see the entire dormitory here. Access
    to the student *record* - guardian contacts, medical notes - stays
    group-scoped in the selector above.
    """
    base = StudentProfile.objects.select_related("user", "group")
    if not include_archived:
        base = base.active()

    if not user or not user.is_authenticated or not user.is_active:
        return base.none()

    if user.role == Role.STUDENT:
        return base.filter(user=user)

    if user.is_superuser or user.has_capability(Capability.VIEW_PRESENCE):
        return base

    return base.none()


def editable_student_queryset_for_user(user, *, include_archived=False):
    """Students ``user`` may modify at all (field-level rules apply on top)."""
    if not user or not user.is_authenticated:
        return StudentProfile.objects.none()
    if not user.has_capability(Capability.EDIT_STUDENTS):
        return StudentProfile.objects.none()
    return student_queryset_for_user(user, include_archived=include_archived)


def can_view_student(user, student):
    return student_queryset_for_user(user, include_archived=True).filter(pk=student.pk).exists()


def can_edit_student(user, student):
    return (
        editable_student_queryset_for_user(user, include_archived=True)
        .filter(pk=student.pk)
        .exists()
    )


def editable_fields_for(user, student=None):
    """Which student fields ``user`` may write directly.

    Teachers get a narrow whitelist; anything else they want changed becomes a
    StudentChangeRequest.
    """
    if not user or not user.is_authenticated:
        return frozenset()
    if user.is_superuser or user.role in {Role.ADMIN, Role.MANAGEMENT}:
        return frozenset(
            f.name
            for f in StudentProfile._meta.get_fields()
            if getattr(f, "editable", False) and f.name not in {"id", "user", "created_at"}
        )
    if user.role == Role.TEACHER and user.has_capability(Capability.EDIT_STUDENTS):
        return frozenset(StudentProfile.TEACHER_EDITABLE_FIELDS)
    return frozenset()


def can_delete_student(user, student):
    """Permanent deletion, unlike archiving, wipes the student's history too
    (spec 7 is the reason archiving exists at all) - so it stays behind its
    own capability, admin-only the same way ``DELETE_USERS`` is.
    """
    if not user or not user.is_authenticated:
        return False
    if not user.has_capability(Capability.DELETE_STUDENTS):
        return False
    return can_view_student(user, student)


def can_view_sensitive(user, student=None):
    return bool(
        user
        and user.is_authenticated
        and user.has_capability(Capability.VIEW_SENSITIVE_STUDENT_DATA)
    )


def student_for_user(user):
    """The student profile belonging to ``user``, if they are a student."""
    return getattr(user, "student_profile", None)


def change_request_queryset_for_user(user):
    from .models import StudentChangeRequest

    base = StudentChangeRequest.objects.select_related(
        "student", "requested_by", "reviewed_by"
    )
    if not user or not user.is_authenticated:
        return base.none()
    if user.has_capability(Capability.REVIEW_STUDENT_CHANGES):
        return base
    if user.has_capability(Capability.REQUEST_STUDENT_CHANGES):
        return base.filter(requested_by=user)
    return base.none()

"""Capability-based authorization.

Authorization in this project has two layers and both are mandatory:

1.  **Capability** - "may this user do this kind of thing at all?"  Answered
    here, by :func:`user_capabilities` / ``user.has_capability(...)``.
2.  **Object scope** - "may this user do it to *this* record?"  Answered by the
    queryset helpers in ``apps.students.selectors`` and friends.

A capability check alone is never sufficient for a record a user supplied an
id for. See spec section 58.
"""

from django.db import models


class Role(models.TextChoices):
    ADMIN = "admin", "Adminisztrátor"
    MANAGEMENT = "management", "Vezetőség"
    TEACHER = "teacher", "Nevelőtanár"
    PORTER = "porter", "Portás"
    STUDENT = "student", "Diák"


class Capability:
    """Namespace of capability strings. Never spell these out inline."""

    # Students
    VIEW_STUDENTS = "students.view"
    CREATE_STUDENTS = "students.create"
    EDIT_STUDENTS = "students.edit"
    ARCHIVE_STUDENTS = "students.archive"
    DELETE_STUDENTS = "students.delete"
    VIEW_SENSITIVE_STUDENT_DATA = "students.view_sensitive"
    REVIEW_STUDENT_CHANGES = "students.review_changes"
    REQUEST_STUDENT_CHANGES = "students.request_changes"

    # Presence
    VIEW_PRESENCE = "presence.view"
    EDIT_PRESENCE = "presence.edit"
    VIEW_PRESENCE_HISTORY = "presence.view_history"
    SELF_PRESENCE = "presence.self"
    MANAGE_STATUS_TYPES = "presence.manage_status_types"

    # Rooms & calendar
    VIEW_ROOMS = "rooms.view"
    MANAGE_ROOMS = "rooms.manage"
    MANAGE_ASSIGNMENTS = "rooms.manage_assignments"
    MANAGE_CALENDAR = "calendar.manage"

    # Evening inspection
    VIEW_EVENING_CHECK = "evening_check.view"
    EDIT_EVENING_CHECK = "evening_check.edit"
    REOPEN_EVENING_CHECK = "evening_check.reopen"

    # Room inspection - also the morning presence round (see spec rework)
    VIEW_ROOM_CHECKS = "room_checks.view"
    EDIT_ROOM_CHECKS = "room_checks.edit"
    REOPEN_ROOM_CHECKS = "room_checks.reopen"
    VIEW_ROOM_CHECK_HISTORY = "room_checks.view_history"
    VIEW_OWN_ROOM_CHECKS = "room_checks.view_own"

    # Weekend stays
    REQUEST_WEEKEND_STAY = "weekend.request"
    VIEW_WEEKEND_STAY = "weekend.view"
    REVIEW_WEEKEND_STAY = "weekend.review"
    MANAGE_WEEKEND_STAY = "weekend.manage"
    RUN_WEEKEND_CHECK = "weekend.check"
    REOPEN_WEEKEND_CHECK = "weekend.reopen"

    # Pass rules (who may be given a pass, and by whom)
    VIEW_OWN_PASS_RULE = "pass_rules.view_own"
    VIEW_PASS_RULES = "pass_rules.view"
    MANAGE_PASS_RULES = "pass_rules.manage"

    # People
    VIEW_TEACHERS = "people.view_teachers"
    MANAGE_TEACHERS = "people.manage_teachers"
    MANAGE_GROUPS = "people.manage_groups"

    # Data / reporting / administration
    MANAGE_DATA = "data.manage"
    IMPORT_DATA = "data.import"
    EXPORT_DATA = "data.export"
    VIEW_REPORTS = "reports.view"
    VIEW_AUDIT_LOG = "audit.view"
    MANAGE_USERS = "system.manage_users"
    DELETE_USERS = "system.delete_users"
    MANAGE_SETTINGS = "system.manage_settings"
    MANAGE_MODULES = "system.manage_modules"
    MANAGE_MAINTENANCE = "system.maintenance"
    DELETE_ALL_DATA = "system.delete_all_data"


ALL_CAPABILITIES = frozenset(
    value
    for name, value in vars(Capability).items()
    if not name.startswith("_") and isinstance(value, str)
)


_STUDENT = frozenset(
    {
        Capability.SELF_PRESENCE,
        Capability.VIEW_OWN_PASS_RULE,
        Capability.VIEW_OWN_ROOM_CHECKS,
        Capability.REQUEST_WEEKEND_STAY,
    }
)

_PORTER = frozenset(
    {
        Capability.VIEW_PRESENCE,
        Capability.VIEW_STUDENTS,
        Capability.VIEW_PASS_RULES,
    }
)

_TEACHER = frozenset(
    {
        Capability.VIEW_STUDENTS,
        Capability.EDIT_STUDENTS,
        Capability.VIEW_SENSITIVE_STUDENT_DATA,
        Capability.REQUEST_STUDENT_CHANGES,
        Capability.VIEW_PRESENCE,
        Capability.EDIT_PRESENCE,
        Capability.VIEW_PRESENCE_HISTORY,
        Capability.VIEW_ROOMS,
        Capability.VIEW_EVENING_CHECK,
        Capability.EDIT_EVENING_CHECK,
        Capability.VIEW_ROOM_CHECKS,
        Capability.EDIT_ROOM_CHECKS,
        Capability.VIEW_ROOM_CHECK_HISTORY,
        Capability.VIEW_WEEKEND_STAY,
        Capability.REVIEW_WEEKEND_STAY,
        Capability.RUN_WEEKEND_CHECK,
        Capability.VIEW_PASS_RULES,
        Capability.MANAGE_PASS_RULES,
        Capability.VIEW_TEACHERS,
        Capability.VIEW_REPORTS,
    }
)

_MANAGEMENT = frozenset(
    _TEACHER
    | {
        Capability.CREATE_STUDENTS,
        Capability.ARCHIVE_STUDENTS,
        Capability.REVIEW_STUDENT_CHANGES,
        Capability.MANAGE_ROOMS,
        Capability.MANAGE_ASSIGNMENTS,
        Capability.MANAGE_CALENDAR,
        Capability.MANAGE_STATUS_TYPES,
        Capability.REOPEN_EVENING_CHECK,
        Capability.REOPEN_ROOM_CHECKS,
        Capability.MANAGE_WEEKEND_STAY,
        Capability.REOPEN_WEEKEND_CHECK,
        Capability.MANAGE_TEACHERS,
        Capability.MANAGE_GROUPS,
        Capability.MANAGE_DATA,
        Capability.IMPORT_DATA,
        Capability.EXPORT_DATA,
        Capability.VIEW_AUDIT_LOG,
        Capability.MANAGE_USERS,
    }
)

# Administrators get everything, including the destructive operations that
# management deliberately does not receive (spec section 3.2).
_ADMIN = frozenset(ALL_CAPABILITIES)

ROLE_CAPABILITIES = {
    Role.STUDENT: _STUDENT,
    Role.PORTER: _PORTER,
    Role.TEACHER: _TEACHER,
    Role.MANAGEMENT: _MANAGEMENT,
    Role.ADMIN: _ADMIN,
}

# Capabilities an operator may hand out individually on top of a role.
GRANTABLE_CAPABILITIES = sorted(
    ALL_CAPABILITIES - {Capability.DELETE_ALL_DATA, Capability.DELETE_USERS}
)


def capabilities_for_role(role):
    return ROLE_CAPABILITIES.get(role, frozenset())


def user_capabilities(user):
    """Resolve the effective capability set for ``user``."""
    if not user or not user.is_authenticated or not user.is_active:
        return frozenset()
    if user.is_superuser:
        return frozenset(ALL_CAPABILITIES)
    granted = set(capabilities_for_role(user.role))
    granted.update(c for c in (user.extra_capabilities or []) if c in ALL_CAPABILITIES)
    granted.difference_update(user.revoked_capabilities or [])
    return frozenset(granted)


# Human-readable names for the capability codes. The profile page lists a
# user's capabilities, and "students.view_sensitive" tells a nevelőtanár
# nothing; this is what they actually see.
CAPABILITY_LABELS = {
    Capability.VIEW_STUDENTS: "Diákok megtekintése",
    Capability.CREATE_STUDENTS: "Diák létrehozása",
    Capability.EDIT_STUDENTS: "Diák szerkesztése",
    Capability.ARCHIVE_STUDENTS: "Diák archiválása",
    Capability.DELETE_STUDENTS: "Diák törlése",
    Capability.VIEW_SENSITIVE_STUDENT_DATA: "Érzékeny diákadatok megtekintése",
    Capability.REVIEW_STUDENT_CHANGES: "Változtatási kérelmek elbírálása",
    Capability.REQUEST_STUDENT_CHANGES: "Változtatási kérelem beadása",
    Capability.VIEW_PRESENCE: "Jelenlét megtekintése",
    Capability.EDIT_PRESENCE: "Jelenlét módosítása",
    Capability.VIEW_PRESENCE_HISTORY: "Jelenléti napló megtekintése",
    Capability.SELF_PRESENCE: "Saját jelenlét jelölése",
    Capability.MANAGE_STATUS_TYPES: "Státuszok kezelése",
    Capability.VIEW_ROOMS: "Szobák megtekintése",
    Capability.MANAGE_ROOMS: "Szobák kezelése",
    Capability.MANAGE_ASSIGNMENTS: "Szobabeosztás kezelése",
    Capability.MANAGE_CALENDAR: "Naptár kezelése",
    Capability.VIEW_EVENING_CHECK: "Esti ellenőrzés megtekintése",
    Capability.EDIT_EVENING_CHECK: "Esti ellenőrzés végzése",
    Capability.REOPEN_EVENING_CHECK: "Esti ellenőrzés újranyitása",
    Capability.VIEW_ROOM_CHECKS: "Reggeli és szobaellenőrzés megtekintése",
    Capability.EDIT_ROOM_CHECKS: "Reggeli és szobaellenőrzés végzése",
    Capability.REOPEN_ROOM_CHECKS: "Reggeli és szobaellenőrzés újranyitása",
    Capability.VIEW_ROOM_CHECK_HISTORY: "Szobarend előzmények megtekintése",
    Capability.VIEW_OWN_ROOM_CHECKS: "Saját szoba ellenőrzéseinek megtekintése",
    Capability.REQUEST_WEEKEND_STAY: "Hétvégi bennmaradás igénylése",
    Capability.VIEW_WEEKEND_STAY: "Hétvégi bennmaradás megtekintése",
    Capability.REVIEW_WEEKEND_STAY: "Hétvégi kérelmek elbírálása",
    Capability.MANAGE_WEEKEND_STAY: "Hétvégi bennmaradás kezelése",
    Capability.RUN_WEEKEND_CHECK: "Hétvégi ellenőrzés végzése",
    Capability.REOPEN_WEEKEND_CHECK: "Hétvégi ellenőrzés újranyitása",
    Capability.VIEW_OWN_PASS_RULE: "Saját kimenő jogosultság megtekintése",
    Capability.VIEW_PASS_RULES: "Kimenő jogosultságok megtekintése",
    Capability.MANAGE_PASS_RULES: "Kimenő jogosultság beállítása",
    Capability.VIEW_TEACHERS: "Nevelőtanárok megtekintése",
    Capability.MANAGE_TEACHERS: "Nevelőtanárok kezelése",
    Capability.MANAGE_GROUPS: "Csoportok kezelése",
    Capability.MANAGE_DATA: "Adatkezelés",
    Capability.IMPORT_DATA: "Adatimport",
    Capability.EXPORT_DATA: "Adatexport",
    Capability.VIEW_REPORTS: "Riportok megtekintése",
    Capability.VIEW_AUDIT_LOG: "Auditnapló megtekintése",
    Capability.MANAGE_USERS: "Felhasználók kezelése",
    Capability.MANAGE_SETTINGS: "Beállítások kezelése",
    Capability.MANAGE_MODULES: "Modulok kezelése",
    Capability.MANAGE_MAINTENANCE: "Karbantartás",
    Capability.DELETE_ALL_DATA: "Teljes adattörlés",
    Capability.DELETE_USERS: "Felhasználók végleges törlése",
}


def capability_label(code):
    """The Hungarian name of a capability, falling back to its raw code."""
    return CAPABILITY_LABELS.get(code, code)


def labelled_capabilities(user):
    """(code, label) pairs for ``user``, sorted for display."""
    return sorted(
        ((code, capability_label(code)) for code in user_capabilities(user)),
        key=lambda pair: pair[1],
    )

"""Permission-aware navigation.

Navigation is generated from backend capabilities and module state, so it can
never advertise something the user cannot actually open (spec sections 41-42).
Hiding a link is a UX nicety; every view enforces the same rules itself.
"""

from dataclasses import dataclass, field

from django.urls import reverse

from apps.accounts.capabilities import Capability
from apps.core.models import ModuleKey, SystemModule


@dataclass
class NavItem:
    label: str
    url_name: str
    capabilities: tuple = ()
    module: str | None = None
    icon: str = ""
    children: list = field(default_factory=list)

    def url(self):
        return reverse(self.url_name)


NAVIGATION = [
    NavItem("Áttekintés", "portal:dashboard", icon="home"),
    NavItem(
        "Jelenlét",
        "presence:current",
        capabilities=(Capability.VIEW_PRESENCE,),
        icon="users",
        children=[
            NavItem("Aktuális jelenlét", "presence:current", (Capability.VIEW_PRESENCE,)),
            NavItem("Jelenléti napló", "presence:log", (Capability.VIEW_PRESENCE_HISTORY,)),
        ],
    ),
    NavItem(
        "Ellenőrzések",
        "inspections:evening_index",
        capabilities=(
            Capability.VIEW_EVENING_CHECK,
            Capability.VIEW_ROOM_CHECKS,
        ),
        icon="clipboard",
        children=[
            NavItem(
                "Esti ellenőrzés",
                "inspections:evening_index",
                (Capability.VIEW_EVENING_CHECK,),
                module=ModuleKey.EVENING_CHECK,
            ),
            # Room condition and each resident's morning status, one screen.
            NavItem(
                "Reggeli ellenőrzés",
                "inspections:roomcheck_index",
                (Capability.VIEW_ROOM_CHECKS,),
                module=ModuleKey.ROOM_CHECKS,
            ),
            NavItem(
                "Szobarend előzmények",
                "inspections:roomcheck_history",
                (Capability.VIEW_ROOM_CHECK_HISTORY,),
                module=ModuleKey.ROOM_CHECKS,
            ),
        ],
    ),
    NavItem(
        "Hétvégi bennmaradás",
        "weekend:index",
        capabilities=(Capability.VIEW_WEEKEND_STAY, Capability.REQUEST_WEEKEND_STAY),
        module=ModuleKey.WEEKEND_STAY,
        icon="calendar",
    ),
    NavItem(
        "Kimenő jogosultság",
        "passes:index",
        capabilities=(Capability.VIEW_PASS_RULES,),
        module=ModuleKey.PASS_RULES,
        icon="key",
    ),
    NavItem(
        "Diákok",
        "students:list",
        capabilities=(Capability.VIEW_STUDENTS,),
        icon="user",
        children=[
            NavItem("Diákok", "students:list", (Capability.VIEW_STUDENTS,)),
            NavItem(
                "Változtatási kérelmek",
                "students:change_requests",
                (Capability.REVIEW_STUDENT_CHANGES, Capability.REQUEST_STUDENT_CHANGES),
            ),
        ],
    ),
    NavItem(
        "Adatkezelés",
        "rooms:list",
        # OR'd so the section still shows for a user who only holds
        # MANAGE_USERS (e.g. via a one-off grant) without MANAGE_DATA too.
        capabilities=(Capability.MANAGE_DATA, Capability.MANAGE_USERS),
        icon="database",
        children=[
            NavItem("Szobák", "rooms:list", (Capability.VIEW_ROOMS,)),
            NavItem("Nevelőtanárok és csoportok", "people:teachers", (Capability.VIEW_TEACHERS,)),
            NavItem("Naptár", "dormcalendar:index", (Capability.MANAGE_CALENDAR,)),
            NavItem("Modulok", "core:modules", (Capability.MANAGE_MODULES,)),
            NavItem("Tanévek", "core:school_years", (Capability.MANAGE_SETTINGS,)),
            NavItem("Felhasználók", "accounts:user_list", (Capability.MANAGE_USERS,)),
        ],
    ),
    NavItem(
        "Riportok",
        "reports:index",
        capabilities=(Capability.VIEW_REPORTS,),
        icon="chart",
    ),
    NavItem(
        "Napló",
        "audit:index",
        capabilities=(Capability.VIEW_AUDIT_LOG,),
        icon="list",
    ),
    NavItem(
        "Karbantartás",
        "core:maintenance",
        capabilities=(Capability.MANAGE_MAINTENANCE,),
        icon="tool",
    ),
]

# What a student sees instead of the staff navigation.
STUDENT_NAVIGATION = [
    NavItem("Kezdőlap", "portal:dashboard", icon="home"),
    NavItem(
        "Hétvégi bennmaradás",
        "weekend:my_stay",
        capabilities=(Capability.REQUEST_WEEKEND_STAY,),
        module=ModuleKey.WEEKEND_STAY,
        icon="calendar",
    ),
    NavItem(
        "Szobám ellenőrzései",
        "inspections:my_room_checks",
        capabilities=(Capability.VIEW_OWN_ROOM_CHECKS,),
        module=ModuleKey.ROOM_CHECKS,
        icon="clipboard",
    ),
    NavItem(
        "Kimenő jogosultságom",
        "passes:mine",
        capabilities=(Capability.VIEW_OWN_PASS_RULE,),
        module=ModuleKey.PASS_RULES,
        icon="key",
    ),
    # Profil and Jelszóváltás are not listed here: base.html appends the
    # account links to every navigation, so a copy here shows up twice.
]


def _visible(item, user, enabled_modules):
    if item.module and item.module not in enabled_modules:
        return False
    if not item.capabilities:
        return True
    return any(user.has_capability(c) for c in item.capabilities)


def _match_score(url, current_path):
    """How well ``url`` describes ``current_path``; 0 means it does not.

    Longest prefix wins, so /students/41/ marks "Diákok" rather than the
    dashboard. The root path is matched exactly, since every path starts
    with "/" and it would otherwise always win.
    """
    if not current_path or not url:
        return 0
    if url == "/":
        return 1 if current_path == "/" else 0
    return len(url) if current_path.startswith(url) else 0


def build_navigation(user, current_path=""):
    """Return the nav tree this user may actually use.

    ``current_path`` marks the entry the user is looking at, so the sidebar
    can show where they are; it is presentation only and never widens what
    the tree contains.
    """
    if not user or not user.is_authenticated:
        return []

    enabled_modules = SystemModule.enabled_keys() | (
        {m for m in ModuleKey.values}
        - set(SystemModule.objects.values_list("key", flat=True))
    )
    source = STUDENT_NAVIGATION if user.is_student else NAVIGATION

    nav = []
    for item in source:
        if not _visible(item, user, enabled_modules):
            continue
        children = [c for c in item.children if _visible(c, user, enabled_modules)]
        if item.children and not children:
            continue
        nav.append(
            {
                "label": item.label,
                "url": children[0].url() if children else item.url(),
                "icon": item.icon,
                "children": [{"label": c.label, "url": c.url()} for c in children],
            }
        )

    _mark_active(nav, current_path)
    return nav


def _mark_active(nav, current_path):
    """Flag the single best match in the tree, parent and child alike."""
    best = (0, None, None)  # score, entry, parent
    for entry in nav:
        # Children are weighed first so that a section pointing at its own
        # first child (the usual case) marks the child as the current page
        # and the parent merely as the section containing it.
        candidates = [(c, entry) for c in entry["children"]] + [(entry, None)]
        for candidate, parent in candidates:
            score = _match_score(candidate["url"], current_path)
            if score > best[0]:
                best = (score, candidate, parent)

    _, entry, parent = best
    if entry is not None:
        entry["active"] = True
        # A matched child marks its section header too, but as "in this
        # section" rather than "this is the page" - two fully highlighted
        # rows would read as two current pages.
        if parent is not None:
            parent["in_section"] = True

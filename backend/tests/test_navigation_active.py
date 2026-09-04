"""The sidebar marks where you are.

Presentation only - marking an entry never widens what the tree contains,
which stays governed by capabilities and module state (spec sections 41-42).
The interesting part is the tie-break: most sections point at their own first
child, so parent and child carry the same URL and something has to decide
which of the two reads as "the current page".
"""

import pytest
from django.urls import reverse

from apps.accounts.capabilities import Role
from apps.portal.navigation import build_navigation

from . import factories as f

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin():
    f.seed_modules()
    return f.make_user(Role.ADMIN, is_superuser=True, is_staff=True)


def _find(nav, label):
    return next(item for item in nav if item["label"] == label)


def test_nothing_is_marked_for_an_unknown_path(admin):
    nav = build_navigation(admin, current_path="/nowhere/")
    assert not any(item.get("active") or item.get("in_section") for item in nav)


def test_the_dashboard_is_marked_on_the_dashboard(admin):
    nav = build_navigation(admin, current_path=reverse("portal:dashboard"))
    assert _find(nav, "Áttekintés")["active"]


def test_the_dashboard_is_not_marked_everywhere_else(admin):
    """The dashboard lives at "/", which prefixes every other path."""
    nav = build_navigation(admin, current_path=reverse("students:list"))
    assert not _find(nav, "Áttekintés").get("active")


def test_the_longest_matching_entry_wins(admin):
    """A student's own page is still "Diákok", not the dashboard."""
    nav = build_navigation(admin, current_path="/students/41/")
    students = _find(nav, "Diákok")
    assert students["in_section"]
    assert _find(nav, "Áttekintés").get("active") is None


def test_a_child_page_marks_the_child_and_its_section(admin):
    nav = build_navigation(admin, current_path=reverse("students:change_requests"))
    students = _find(nav, "Diákok")

    child = next(c for c in students["children"] if c["label"] == "Változtatási kérelmek")
    assert child["active"]
    # The header shows you are inside the section, but is not itself "the page":
    # two fully marked rows would read as two current pages.
    assert students["in_section"]
    assert students.get("active") is None


def test_a_section_pointing_at_its_own_first_child_marks_the_child(admin):
    """"Diákok" the section and "Diákok" the page share a URL; the page wins."""
    nav = build_navigation(admin, current_path=reverse("students:list"))
    students = _find(nav, "Diákok")

    assert students["children"][0]["active"]
    assert students["in_section"]
    assert students.get("active") is None


def test_exactly_one_entry_is_ever_the_current_page(admin):
    for path in ["/students/", "/rooms/", reverse("portal:dashboard"), "/students/41/edit/"]:
        nav = build_navigation(admin, current_path=path)
        marked = [i for i in nav if i.get("active")]
        marked += [c for i in nav for c in i["children"] if c.get("active")]
        assert len(marked) <= 1, path


def test_marking_does_not_change_which_entries_exist(admin):
    plain = build_navigation(admin)
    marked = build_navigation(admin, current_path=reverse("students:list"))
    assert [i["label"] for i in plain] == [i["label"] for i in marked]


def test_the_sidebar_renders_the_current_page_as_aria_current(client, admin):
    client.force_login(admin)
    body = client.get(reverse("students:list")).content.decode()
    assert 'aria-current="page"' in body

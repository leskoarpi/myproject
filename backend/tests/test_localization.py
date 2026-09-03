"""Regression tests for the Hungarian-only UI pass.

These pin the three places that needed more than a string swap: the two
"new record" forms that used to derive English labels from unlabeled form
fields, the audit trail's raw-dict rendering, and the ad-hoc report table
headers. A broad per-role sweep guards against new English strings creeping
back in on any page a real user reaches.
"""

import re

import pytest
from django.urls import reverse

from apps.accounts.capabilities import Role
from apps.accounts.capabilities import capability_label, labelled_capabilities
from apps.audit.services import REDACTED, record_audit
from apps.audit.templatetags.audit_tags import format_change
from apps.reports.templatetags.report_tags import column_label

from . import factories as f

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# The keyword-only `label` bug: forms.CharField(label=..., ...) not
# forms.CharField(..., ...) - the latter raises TypeError at render time.
# --------------------------------------------------------------------------


def test_student_create_form_has_hungarian_labels(client):
    f.seed_status_types()
    f.seed_modules()
    f.make_school_year()
    admin = f.make_user(Role.ADMIN)
    client.force_login(admin)

    response = client.get(reverse("students:create"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Felhasználónév" in body
    assert "E-mail cím" in body
    assert "Ideiglenes jelszó" in body


def test_teacher_create_form_has_hungarian_labels(client):
    f.seed_status_types()
    f.seed_modules()
    admin = f.make_user(Role.ADMIN)
    client.force_login(admin)

    response = client.get(reverse("people:teacher_create"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Felhasználónév" in body
    assert "E-mail cím" in body


# --------------------------------------------------------------------------
# The audit trail: model fields resolve through verbose_name; everything
# else falls back gracefully instead of crashing.
# --------------------------------------------------------------------------


def test_format_change_translates_model_fields():
    room = f.make_room()
    rendered = format_change({"rating": 4, "problems": ""}, "inspections.RoomCheck")
    assert rendered == "értékelés: 4; hibák: "


def test_format_change_falls_back_to_the_raw_key_for_unknown_payloads():
    # Ad-hoc dicts (progress counters, import summaries) are not model fields.
    rendered = format_change({"rows": 3, "unknown_future_key": 1}, "reports")
    assert "sorok: 3" in rendered
    assert "unknown_future_key: 1" in rendered  # unmapped key, not a crash


def test_format_change_renders_booleans_in_hungarian():
    rendered = format_change({"is_active": True, "note": None}, "rooms.Room")
    assert "igen" in rendered
    assert "—" in rendered


def test_format_change_passes_through_non_dict_values():
    assert format_change("plain string", "rooms.Room") == "plain string"


def test_redacted_marker_is_hungarian():
    assert REDACTED == "[nem naplózva]"


def test_audit_log_uses_the_hungarian_redaction_marker():
    admin = f.make_user(Role.ADMIN)
    student = f.make_student()
    entry = record_audit(
        user=admin,
        action="update",
        target=student,
        new_value={"medical_notes": "asthma"},
    )
    assert entry.new_value["medical_notes"] == "[nem naplózva]"


# --------------------------------------------------------------------------
# Report table headers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,expected",
    [
        ("name", "Név"),
        ("room", "Szoba"),
        ("eligibility", "Beállítás"),
        ("set_by", "Beállította"),
    ],
)
def test_column_label_translates_known_keys(key, expected):
    assert column_label(key) == expected


def test_column_label_falls_back_to_the_raw_key():
    assert column_label("some_future_column") == "some_future_column"


# --------------------------------------------------------------------------
# Capability labels on the profile page
# --------------------------------------------------------------------------


def test_capability_label_is_hungarian():
    from apps.accounts.capabilities import Capability

    assert capability_label(Capability.VIEW_SENSITIVE_STUDENT_DATA) == (
        "Érzékeny diákadatok megtekintése"
    )


def test_labelled_capabilities_covers_every_granted_capability():
    """Every capability a role can actually hold has a real label, not its raw code."""
    from apps.accounts.capabilities import ALL_CAPABILITIES, CAPABILITY_LABELS

    missing = ALL_CAPABILITIES - set(CAPABILITY_LABELS)
    assert not missing, f"capabilities without a Hungarian label: {sorted(missing)}"


def test_profile_page_shows_labels_not_raw_codes(client):
    admin = f.make_user(Role.ADMIN)
    client.force_login(admin)
    body = client.get(reverse("accounts:profile")).content.decode()

    assert "Diák létrehozása" in body
    assert "students.create" not in body


# --------------------------------------------------------------------------
# Broad sweep: every role, every page it can reach, no leftover English.
# --------------------------------------------------------------------------

# A conservative word list: common English UI vocabulary that has no
# legitimate reason to appear in body text or form labels/placeholders/titles
# on this site. CSV column-contract <code> blocks are excluded before matching,
# since those are literal identifiers, not prose (see the CSV import note).
_EN_WORD = re.compile(
    r"\b(missing|unknown|disabled|capability|required|cannot|guardian|"
    r"emergency|medical|birth\s*date|school\s*name|school\s*class|move\s*in|"
    r"move\s*out|primary\s*group|is\s*active|full\s*name|username|"
    r"too\s+many\s+failed)\b",
    re.I,
)


def _strip_code_blocks(html):
    return re.sub(r"<code>.*?</code>", " ", html, flags=re.S)


def _strip_scripts(html):
    return re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)


@pytest.fixture
def localization_world():
    f.seed_status_types()
    f.seed_modules()
    f.make_school_year()
    group = f.make_group()
    return {
        "admin": f.make_user(Role.ADMIN),
        "management": f.make_user(Role.MANAGEMENT),
        "teacher": f.make_teacher(group=group).user,
        "porter": f.make_user(Role.PORTER),
        "student": f.make_student(group=group).user,
    }


PAGES_BY_ROLE = {
    "admin": [
        "portal:dashboard",
        "students:create",
        "students:list",
        "rooms:list",
        "rooms:create",
        "rooms:import",
        "people:teachers",
        "people:teacher_create",
        "people:teacher_import",
        "accounts:profile",
        "audit:index",
        "reports:index",
    ],
    "management": ["portal:dashboard", "reports:index", "passes:index"],
    "teacher": ["portal:dashboard", "presence:current", "passes:index"],
    "porter": ["portal:dashboard", "presence:current"],
    "student": ["portal:dashboard", "accounts:profile"],
}


def test_no_english_leaks_across_pages_and_roles(client, localization_world):
    findings = {}
    for role, url_names in PAGES_BY_ROLE.items():
        user = localization_world[role]
        client.force_login(user)
        for url_name in url_names:
            response = client.get(reverse(url_name))
            if response.status_code != 200:
                continue
            body = _strip_code_blocks(_strip_scripts(response.content.decode()))
            visible = re.sub(r"<[^>]+>", " ", body)
            hits = sorted(set(_EN_WORD.findall(visible)))
            if hits:
                findings[f"{role}:{url_name}"] = hits

    assert not findings, f"English text found: {findings}"

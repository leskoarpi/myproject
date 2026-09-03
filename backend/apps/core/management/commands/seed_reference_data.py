"""Create the reference data a fresh installation needs.

Idempotent: safe to run on every deploy. It never overwrites operator edits,
it only fills in what is missing.
"""

import datetime as dt

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.models import ModuleKey, SchoolYear, SystemModule
from apps.dormcalendar.services import ensure_calendar_days
from apps.presence.models import StatusType

STATUS_TYPES = [
    # code, label, short, color, counts_as_inside, student_selectable, order
    ("inside", "Bent", "B", "#1a7f52", True, True, 10),
    # Recorded during the morning round when there is no school that day and
    # the student stays in the building.
    ("no_school", "Nincs tanítás", "NT", "#0f766e", True, False, 15),
    ("outside", "Kint", "K", "#9a6407", False, True, 20),
    ("home", "Hazament", "H", "#7c3aed", False, True, 30),
    ("school", "Iskolában", "I", "#1f4e79", False, True, 40),
    ("doctor", "Orvosnál", "O", "#0e7490", False, True, 50),
    ("night_leave", "Éjszakai kimenő", "É", "#b45309", False, True, 60),
    ("absent", "Hiányzik", "X", "#b3261e", False, False, 65),
    ("other", "Egyéb", "E", "#64748b", False, True, 70),
]

MODULES = [
    (ModuleKey.EVENING_CHECK, "Esti ellenőrzés", "Emeletenkénti esti jelenlét-ellenőrzés."),
    (
        ModuleKey.ROOM_CHECKS,
        "Reggeli és szobaellenőrzés",
        "Szobarend értékelése és a lakók reggeli státusza egy menetben.",
    ),
    (
        ModuleKey.WEEKEND_STAY,
        "Hétvégi bennmaradás",
        "Hétvégi jelentkezés, elbírálás és éjszakai ellenőrzések.",
    ),
    (
        ModuleKey.PASS_RULES,
        "Kimenő jogosultság",
        "Ki kaphat kimenőt és kitől - minden nevelőtanár és vezető látja.",
    ),
]

# Module rows left behind by the earlier design.
RETIRED_MODULE_KEYS = ["morning_check", "leave_permissions"]


class Command(BaseCommand):
    help = "Seed status types, modules and (optionally) the current school year."

    def add_arguments(self, parser):
        parser.add_argument(
            "--school-year",
            help="Create/activate a school year, e.g. 2025/2026.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        created_statuses = 0
        for code, label, short, color, inside, selectable, order in STATUS_TYPES:
            _, created = StatusType.objects.get_or_create(
                code=code,
                defaults={
                    "label": label,
                    "short_label": short,
                    "color": color,
                    "counts_as_inside": inside,
                    "student_selectable": selectable,
                    "sort_order": order,
                    "is_default": code == "inside",
                    "requires_note": code == "other",
                },
            )
            created_statuses += int(created)

        # Drop modules that no longer exist so they cannot be toggled.
        SystemModule.objects.filter(key__in=RETIRED_MODULE_KEYS).delete()

        created_modules = 0
        for key, name, description in MODULES:
            _, created = SystemModule.objects.get_or_create(
                key=key, defaults={"name": name, "description": description}
            )
            created_modules += int(created)

        year_name = options.get("school_year")
        if year_name:
            start_year = int(year_name.split("/")[0])
            year, _ = SchoolYear.objects.get_or_create(
                name=year_name,
                defaults={
                    "start_date": dt.date(start_year, 9, 1),
                    "end_date": dt.date(start_year + 1, 6, 30),
                },
            )
            if not SchoolYear.objects.filter(is_active=True).exclude(pk=year.pk).exists():
                year.is_active = True
                year.save(update_fields=["is_active"])
            days = ensure_calendar_days(year)
            self.stdout.write(f"School year {year.name}: {days} calendar day(s) created.")

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete: {created_statuses} status type(s), "
                f"{created_modules} module(s) created."
            )
        )

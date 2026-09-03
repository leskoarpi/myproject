"""Weekends are checked at night only now.

The Saturday-morning and Sunday-morning rounds were removed from
``WeekendCheckType``. Their sessions and results would otherwise linger as rows
whose type no longer renders, so they are deleted here. This is destructive by
design; the restore point in RESTORE.md holds the old data.
"""

from django.db import migrations

RETIRED_TYPES = ["saturday_morning", "sunday_morning"]


def drop_morning_checks(apps, schema_editor):
    WeekendCheckSession = apps.get_model("weekend", "WeekendCheckSession")
    WeekendCheck = apps.get_model("weekend", "WeekendCheck")

    sessions = WeekendCheckSession.objects.filter(check_type__in=RETIRED_TYPES)
    WeekendCheck.objects.filter(session__in=sessions).delete()
    sessions.delete()


def noop(apps, schema_editor):
    """Nothing to restore: the deleted rows are gone by design."""


class Migration(migrations.Migration):
    dependencies = [("weekend", "0002_alter_weekendchecksession_check_type")]

    operations = [migrations.RunPython(drop_morning_checks, noop)]

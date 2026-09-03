"""Remove module rows for features that no longer exist.

``morning_check`` was folded into the room round and ``leave_permissions`` was
replaced by per-student pass rules. Leaving the rows behind would show
toggles for modules nothing reads.
"""

from django.db import migrations

RETIRED_KEYS = ["morning_check", "leave_permissions"]


def drop_retired_modules(apps, schema_editor):
    SystemModule = apps.get_model("core", "SystemModule")
    SystemModule.objects.filter(key__in=RETIRED_KEYS).delete()


def noop(apps, schema_editor):
    """The seed command recreates whatever modules the code declares."""


class Migration(migrations.Migration):
    dependencies = [("core", "0002_alter_systemmodule_key")]

    operations = [migrations.RunPython(drop_retired_modules, noop)]

"""Template filter for the audit log's "Változás" column.

``AuditLog.new_value``/``old_value`` holds two different shapes of payload:

* a snapshot of real model fields (via :func:`apps.audit.services.model_snapshot`),
  keyed by the model's own field names
* an ad-hoc status dict a service built by hand (progress counters, import
  summaries, ...)

Model field names already have a Hungarian ``verbose_name`` (set alongside
every model in this project); ad-hoc dict keys do not, since they were never
model fields to begin with. This filter renders both as "címke: érték" pairs
instead of a raw Python dict repr, translating whichever vocabulary applies.
"""

from django import template
from django.apps import apps as django_apps

register = template.Library()

# Keys that are never a model field - service-built status/summary dicts.
AD_HOC_LABELS = {
    "weekend": "hétvége",
    "friday": "péntek",
    "saturday": "szombat",
    "student": "diák",
    "fields": "mezők",
    "created": "létrehozva",
    "updated": "frissítve",
    "skipped": "kihagyva",
    "rows": "sorok",
    "sheets": "munkalapok",
    "format": "formátum",
    "reports": "riportok",
    "expected": "várt",
    "recorded": "rögzítve",
    "missing": "hiányzik",
    "inside": "bent",
    "outside": "kint",
    "complete": "kész",
    "percent": "százalék",
    "rooms_total": "szobák összesen",
    "rooms_done": "szobák kész",
    "students_total": "diákok összesen",
    "students_done": "diákok kész",
    "students_missing": "diákok hiányoznak",
}


def _label_for(key, model):
    if model is not None:
        try:
            return str(model._meta.get_field(key).verbose_name)
        except Exception:
            pass
    return AD_HOC_LABELS.get(key, key)


def _format_value(value):
    if value is True:
        return "igen"
    if value is False:
        return "nem"
    if value is None:
        return "—"
    return value


@register.filter
def format_change(value, target_type=""):
    """Render an audit change payload with Hungarian labels."""
    if not isinstance(value, dict):
        return value

    model = None
    if target_type and "." in target_type:
        app_label, _, model_name = target_type.partition(".")
        try:
            model = django_apps.get_model(app_label, model_name)
        except LookupError:
            model = None

    parts = [f"{_label_for(key, model)}: {_format_value(val)}" for key, val in value.items()]
    return "; ".join(parts)

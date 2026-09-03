from django import template

register = template.Library()


@register.filter
def cell(row, key):
    """Look up a report row's column by name.

    Report rows are plain dicts built in apps.reports.queries; the template
    language cannot index them by a loop variable on its own.
    """
    if hasattr(row, "get"):
        value = row.get(key, "")
    else:
        value = getattr(row, key, "")
    if value is True:
        return "igen"
    if value is False:
        return "nem"
    return "" if value is None else value


@register.filter
def dict_get(mapping, key):
    """Look up a dict by a loop variable.

    The monthly worksheets keep their cells in ``{day: value}`` dicts; the
    template language cannot index those by the day it is iterating.
    """
    if mapping is None:
        return ""
    value = mapping.get(key, "")
    return "" if value is None else value


# Report rows are plain dicts (apps.reports.queries), so their keys are Python
# identifiers, not display text - the same tension as a JSON API's field
# names. This is the display-only translation; the CSV/JSON export keeps the
# literal key as its column header, since that is a machine-readable export
# contract another program may already parse.
COLUMN_LABELS = {
    "name": "Név",
    "room": "Szoba",
    "group": "Csoport",
    "status": "Státusz",
    "note": "Megjegyzés",
    "notes": "Megjegyzés",
    "since": "Ekkortól",
    "inside": "Bent",
    "from": "Előző",
    "to": "Új",
    "reason": "Ok",
    "source": "Forrás",
    "at": "Időpont",
    "by": "Rögzítette",
    "floor": "Emelet",
    "capacity": "Férőhely",
    "occupied": "Foglalt",
    "free": "Szabad",
    "active": "Aktív",
    "date": "Dátum",
    "problems": "Hibák",
    "rating": "Értékelés",
    "room__number": "Szoba",
    "room__floor": "Emelet",
    "average": "Átlag",
    "checks": "Ellenőrzés",
    "guest": "Vendég",
    "friday": "Péntek",
    "saturday": "Szombat",
    "eligibility": "Beállítás",
    "set_by": "Beállította",
    "set_at": "Beállítva",
}


@register.filter
def column_label(key):
    """The Hungarian header for a report column key, falling back to the key."""
    return COLUMN_LABELS.get(key, key)

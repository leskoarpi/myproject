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

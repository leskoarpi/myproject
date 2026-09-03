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

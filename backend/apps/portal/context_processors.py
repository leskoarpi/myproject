from apps.core.models import ModuleKey, SystemModule

from .navigation import build_navigation


def navigation(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav_items": [], "enabled_modules": set()}

    known = set(SystemModule.objects.values_list("key", flat=True))
    enabled = SystemModule.enabled_keys() | (set(ModuleKey.values) - known)
    return {
        "nav_items": build_navigation(user, current_path=request.path),
        "enabled_modules": enabled,
        "site_name": "Deák Koli",
    }

"""Reusable capability guards for views.

These check *capabilities* only. Anything that loads a record by id must also
run it through the matching scoped queryset (spec section 58).
"""

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from apps.core.models import SystemModule


def require_capabilities(*capabilities, require_all=True):
    """View decorator enforcing one or more capabilities."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            check = all if require_all else any
            if not check(user.has_capability(c) for c in capabilities):
                raise PermissionDenied("Ehhez a művelethez nincs jogosultságod.")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def require_module(module_key):
    """View decorator refusing access while a module is switched off.

    Navigation hiding is not access control; every view of a switchable module
    carries this guard (spec section 41).
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not SystemModule.is_module_enabled(module_key):
                raise PermissionDenied("Ez a modul ki van kapcsolva.")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


class CapabilityRequiredMixin:
    """Class-based-view counterpart of :func:`require_capabilities`."""

    required_capabilities = ()
    require_all_capabilities = True
    required_module = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if self.required_module and not SystemModule.is_module_enabled(self.required_module):
            raise PermissionDenied("Ez a modul ki van kapcsolva.")
        caps = self.required_capabilities
        if caps:
            check = all if self.require_all_capabilities else any
            if not check(request.user.has_capability(c) for c in caps):
                raise PermissionDenied("Ehhez a művelethez nincs jogosultságod.")
        return super().dispatch(request, *args, **kwargs)

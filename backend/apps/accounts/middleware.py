from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    """Newly created accounts must set their own password before continuing.

    Preserves the behaviour of the legacy system (spec section 4) while keeping
    logout, the password change page itself and static assets reachable.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.must_change_password:
            allowed = {
                reverse("accounts:password_change"),
                reverse("accounts:logout"),
            }
            path = request.path
            if path not in allowed and not path.startswith(("/static/", "/media/")):
                return redirect("accounts:password_change")
        return self.get_response(request)

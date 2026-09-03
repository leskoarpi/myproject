"""Makes the acting user and client IP available to service-layer audit calls
without threading a request object through the whole domain layer."""

import contextvars

_current_request = contextvars.ContextVar("audit_current_request", default=None)


def get_current_request():
    return _current_request.get()


def get_client_ip(request=None):
    request = request or get_current_request()
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current_request.set(request)
        try:
            return self.get_response(request)
        finally:
            _current_request.reset(token)

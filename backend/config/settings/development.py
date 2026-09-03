"""Development settings: production parity with the sharp edges filed down."""

from .base import *  # noqa: F403
from .base import env_bool

DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = ["*"]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Development runs over plain HTTP behind the local nginx container.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

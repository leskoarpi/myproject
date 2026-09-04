"""Production settings. Fails fast when required secrets are missing."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import env, env_bool, env_int, env_list

DEBUG = False

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set in production.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set in production.")

if not env("POSTGRES_PASSWORD"):
    raise ImproperlyConfigured("POSTGRES_PASSWORD must be set in production.")

# TLS is terminated at the reverse proxy; trust its forwarding header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)

# Secure cookies are the right default and the only correct setting once TLS
# is in front - which, behind Dokploy's Traefik with a domain attached, it is
# from the first deploy. The switch exists for the gap before that: reached
# over plain http, a browser withholds the session and CSRF cookies, so
# nobody can log in and it looks like a rejected password rather than a
# setting. Flip it in the Environment tab, and back on with the certificate.
SECURE_COOKIES = env_bool("DJANGO_SECURE_COOKIES", True)
SESSION_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_SECURE = SECURE_COOKIES
SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 60 * 60 * 24 * 365)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Content-hashed filenames, so a deploy cannot leave browsers on stale CSS/JS.
# nginx serves hashed files with a one-year immutable cache and everything else
# with no-cache; run collectstatic on every deploy or {% static %} will fail.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
    },
}

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "noreply@example.invalid")

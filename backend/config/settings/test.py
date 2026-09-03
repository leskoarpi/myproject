"""Test settings. Still PostgreSQL - the schema relies on Postgres constraints."""

from .base import *  # noqa: F403

DEBUG = False
ALLOWED_HOSTS = ["*"]

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Plain static storage: the tests render templates without running collectstatic.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

LOGGING["root"]["level"] = "ERROR"  # noqa: F405

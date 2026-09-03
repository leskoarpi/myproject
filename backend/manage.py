#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - import guard
        raise ImportError(
            "Couldn't import Django. Is it installed and is the virtual "
            "environment active?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

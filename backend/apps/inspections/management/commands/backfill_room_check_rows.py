"""One-off repair for a fixed bug: a room added to a floor after that day's
room-check session already existed never got a RoomCheck row, so it could
never be rated and silently never appeared on the sheet or in the monthly
worksheet. sync_room_check_rows() is idempotent (bulk_create with
ignore_conflicts), so running this against every existing session is safe -
rooms that already have a row, and any rating already on them, are untouched.
"""

from django.core.management.base import BaseCommand

from apps.inspections.models import RoomCheckSession
from apps.inspections.services import sync_room_check_rows


class Command(BaseCommand):
    help = "Back-fill missing RoomCheck rows on existing room-check sessions."

    def handle(self, *args, **options):
        sessions = RoomCheckSession.objects.all()
        total_added = 0
        touched_sessions = 0

        for session in sessions:
            before = session.checks.count()
            sync_room_check_rows(session)
            after = session.checks.count()
            if after > before:
                touched_sessions += 1
                total_added += after - before
                self.stdout.write(
                    f"  {session.date} floor {session.floor}: +{after - before} room(s)"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Checked {sessions.count()} session(s); backfilled {total_added} "
                f"missing room row(s) across {touched_sessions} session(s)."
            )
        )

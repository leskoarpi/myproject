"""Report query/service layer.

Reports are assembled here and merely rendered by views or serialized by the
exporters - no SQL or aggregation lives in a template (spec section 69).
Every query starts from a user-scoped queryset.
"""

import datetime as dt

from django.db.models import Avg, Count, Q

from apps.inspections.models import RoomCheck
from apps.leave_permissions.models import PassRuleHistory, StudentPassRule
from apps.presence.models import PresenceEvent, StudentPresence
from apps.rooms.models import Room
from apps.students.selectors import presence_queryset_for_user, student_queryset_for_user


def current_presence_report(user):
    students = presence_queryset_for_user(user)
    rows = (
        StudentPresence.objects.filter(student__in=students)
        .select_related("student", "status", "student__group")
        .prefetch_related("student__room_assignments__room")
        .order_by("student__full_name")
    )
    return [
        {
            "name": p.student.full_name,
            "room": p.student.current_room.number if p.student.current_room else "",
            "group": p.student.group.name if p.student.group else "",
            "status": p.status.label,
            "inside": p.status.counts_as_inside,
            "since": p.changed_at,
            "note": p.note,
        }
        for p in rows
    ]


def presence_history_report(user, *, start=None, end=None, student=None):
    students = presence_queryset_for_user(user)
    events = PresenceEvent.objects.filter(student__in=students).select_related(
        "student", "old_status", "new_status", "created_by"
    )
    if student is not None:
        events = events.filter(student=student)
    if start:
        events = events.filter(created_at__date__gte=start)
    if end:
        events = events.filter(created_at__date__lte=end)
    return [
        {
            "name": e.student.full_name,
            "from": e.old_status.label if e.old_status else "",
            "to": e.new_status.label,
            "reason": e.reason,
            "source": e.get_source_display(),
            "at": e.created_at,
            "by": e.created_by.display_name if e.created_by else "",
        }
        for e in events[:5000]
    ]


def occupancy_report():
    rooms = Room.objects.annotate(
        occupancy_count=Count("assignments", filter=Q(assignments__is_active=True))
    ).order_by("floor", "number")
    return [
        {
            "room": r.number,
            "floor": r.floor,
            "capacity": r.capacity,
            "occupied": r.occupancy_count,
            "free": max(r.capacity - r.occupancy_count, 0),
            "active": r.is_active,
        }
        for r in rooms
    ]


def room_check_report(*, start=None, end=None):
    checks = RoomCheck.objects.select_related("room", "session", "checked_by")
    if start:
        checks = checks.filter(session__date__gte=start)
    if end:
        checks = checks.filter(session__date__lte=end)
    return [
        {
            "date": c.session.date,
            "room": c.room.number,
            "floor": c.room.floor,
            "rating": c.rating,
            "problems": c.problems,
            "notes": c.notes,
            "by": c.checked_by.display_name if c.checked_by else "",
        }
        for c in checks.order_by("-session__date", "room__number")[:5000]
    ]


def room_rating_averages(*, start=None, end=None):
    checks = RoomCheck.objects.filter(rating__isnull=False)
    if start:
        checks = checks.filter(session__date__gte=start)
    if end:
        checks = checks.filter(session__date__lte=end)
    return list(
        checks.values("room__number", "room__floor")
        .annotate(average=Avg("rating"), checks=Count("id"))
        .order_by("average")
    )


def weekend_stay_report(user, weekend_start):
    """Scoped like the weekend module itself: a teacher without broader
    weekend access only sees their own groups' stays here too, not every
    student's (spec section 58 - capability alone is never enough)."""
    from apps.weekend.services import weekend_stay_queryset_for_user

    stays = weekend_stay_queryset_for_user(user, weekend_start=weekend_start)
    return [
        {
            "room": s.room_number,
            "name": s.display_name,
            "group": s.group_name,
            "guest": s.is_guest,
            "friday": s.friday_stay,
            "saturday": s.saturday_stay,
            "status": s.get_status_display(),
            "note": s.note,
        }
        for s in stays
    ]


def pass_rule_report(user):
    """Who may be given a pass. Dormitory-wide for staff, like the screen."""
    from apps.leave_permissions.services import pass_rule_queryset_for_user

    rules = pass_rule_queryset_for_user(user)
    return [
        {
            "room": r.student.current_room.number if r.student.current_room else "",
            "name": r.student.full_name,
            "group": r.student.group.code if r.student.group else "",
            "eligibility": r.get_eligibility_display(),
            "note": r.note,
            "set_by": r.set_by.display_name if r.set_by else "",
            "set_at": r.set_at,
        }
        for r in rules.order_by("student__full_name")
    ]


def pass_rule_history_report(user, *, start=None, end=None):
    entries = PassRuleHistory.objects.select_related("student", "created_by")
    if start:
        entries = entries.filter(created_at__date__gte=start)
    if end:
        entries = entries.filter(created_at__date__lte=end)
    return [
        {
            "name": e.student.full_name,
            "from": e.previous_label,
            "to": e.new_label,
            "note": e.new_note,
            "at": e.created_at,
            "by": e.created_by.display_name if e.created_by else "rendszer",
        }
        for e in entries[:5000]
    ]


def default_period():
    today = dt.date.today()
    return today - dt.timedelta(days=30), today


def restricted_pass_count():
    return StudentPassRule.objects.restricted().count()

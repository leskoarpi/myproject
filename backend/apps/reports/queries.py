"""Report query/service layer.

Reports are assembled here and merely rendered by views or serialized by the
exporters - no SQL or aggregation lives in a template (spec section 69).
Every query starts from a user-scoped queryset.
"""

import datetime as dt

from django.db.models import Avg, Count, Q

from apps.inspections.models import MorningResult, MorningSnapshot, RoomCheck
from apps.leave_permissions.models import LeavePermission, LeavePermissionHistory
from apps.presence.models import PresenceEvent, StudentPresence
from apps.rooms.models import Room
from apps.students.selectors import student_queryset_for_user
from apps.weekend.models import WeekendStay


def current_presence_report(user):
    students = student_queryset_for_user(user)
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
    students = student_queryset_for_user(user)
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


def morning_report(snapshot):
    items = snapshot.items.all()
    return [
        {
            "name": i.student_name,
            "room": i.room_number,
            "floor": i.floor,
            "class": i.school_class,
            "evening": i.evening_status_label,
            "cutoff": i.cutoff_status_label,
            "calculated": i.get_calculated_result_display(),
            "final": i.get_final_result_display(),
            "reviewed": i.is_reviewed,
            "note": i.note,
        }
        for i in items
    ]


def morning_absence_summary(*, start, end):
    """How often each student was recorded absent over a period."""
    snapshots = MorningSnapshot.objects.filter(
        calendar_day__date__gte=start, calendar_day__date__lte=end
    )
    from apps.inspections.models import MorningSnapshotItem

    rows = (
        MorningSnapshotItem.objects.filter(snapshot__in=snapshots)
        .values("student_id", "student_name")
        .annotate(
            absences=Count("id", filter=Q(final_result=MorningResult.ABSENT)),
            left_overnight=Count("id", filter=Q(final_result=MorningResult.LEFT_OVERNIGHT)),
            total=Count("id"),
        )
        .order_by("-absences", "student_name")
    )
    return list(rows)


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


def weekend_stay_report(weekend_start):
    stays = WeekendStay.objects.for_weekend(weekend_start).select_related("student", "room")
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


def leave_permission_report(user):
    from apps.leave_permissions.services import leave_permission_queryset_for_user

    permissions = leave_permission_queryset_for_user(user)
    return [
        {
            "name": p.student.full_name,
            "status": p.get_status_display(),
            "type": p.get_permission_type_display(),
            "value": p.value,
            "granted_at": p.granted_at,
            "expires_at": p.expires_at,
            "granted_by": p.granted_by.display_name if p.granted_by else "",
        }
        for p in permissions.order_by("student__full_name")
    ]


def leave_permission_history_report(user, *, start=None, end=None):
    students = student_queryset_for_user(user)
    entries = LeavePermissionHistory.objects.filter(student__in=students).select_related(
        "student", "created_by"
    )
    if start:
        entries = entries.filter(created_at__date__gte=start)
    if end:
        entries = entries.filter(created_at__date__lte=end)
    return [
        {
            "name": e.student.full_name,
            "action": e.get_action_display(),
            "from": e.previous_status,
            "to": e.new_status,
            "value": e.new_value,
            "at": e.created_at,
            "by": e.created_by.display_name if e.created_by else "rendszer",
        }
        for e in entries[:5000]
    ]


def default_period():
    today = dt.date.today()
    return today - dt.timedelta(days=30), today


def active_permission_count():
    from apps.leave_permissions.models import PermissionStatus

    return LeavePermission.objects.filter(status=PermissionStatus.ACTIVE).count()

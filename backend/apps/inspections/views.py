import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.capabilities import Capability
from apps.accounts.permissions import require_capabilities, require_module
from apps.core.models import ModuleKey
from apps.dormcalendar.services import get_or_create_day
from apps.presence.models import StatusType
from apps.rooms.services import floors, students_on_floor

from .models import EveningCheckSession, RoomCheck, RoomCheckSession
from .services import (
    acquire_session_lock,
    close_evening_session,
    close_room_check_session,
    open_evening_session,
    open_room_check_session,
    release_session_lock,
    reopen_evening_session,
    reopen_room_check_session,
    room_check_progress,
    room_check_queryset_for_user,
    save_evening_result,
    save_room_check,
    save_student_morning_status,
    sync_room_check_rows,
)
from .services.evening import evening_overview, evening_session_progress


def _msg(exc):
    return " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


def _parse_date(raw, default=None):
    if not raw:
        return default or timezone.localdate()
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        return default or timezone.localdate()


# --------------------------------------------------------------------------
# Evening
# --------------------------------------------------------------------------


@login_required
@require_module(ModuleKey.EVENING_CHECK)
@require_capabilities(Capability.VIEW_EVENING_CHECK)
def evening_index(request):
    day = get_or_create_day(_parse_date(request.GET.get("date")))
    return render(
        request,
        "inspections/evening_index.html",
        {
            "day": day,
            "overview": evening_overview(day),
            "can_edit": request.user.has_capability(Capability.EDIT_EVENING_CHECK),
            "can_reopen": request.user.has_capability(Capability.REOPEN_EVENING_CHECK),
        },
    )


@login_required
@require_module(ModuleKey.EVENING_CHECK)
@require_capabilities(Capability.VIEW_EVENING_CHECK)
def evening_floor(request, date, floor):
    day = get_or_create_day(_parse_date(date))
    session = EveningCheckSession.objects.filter(calendar_day=day, floor=floor).first()

    if session is None and request.user.has_capability(Capability.EDIT_EVENING_CHECK):
        try:
            session = open_evening_session(calendar_day=day, floor=floor, actor=request.user)
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, _msg(exc))
            return redirect("inspections:evening_index")

    if session is None:
        messages.info(request, "Erre az emeletre még nem indult ellenőrzés.")
        return redirect("inspections:evening_index")

    lock_error = None
    if session.is_open and request.user.has_capability(Capability.EDIT_EVENING_CHECK):
        try:
            session = acquire_session_lock(session=session, actor=request.user)
        except PermissionDenied as exc:
            lock_error = str(exc)
        except ValidationError as exc:
            lock_error = _msg(exc)

    results = {r.student_id: r for r in session.results.select_related("status")}
    students = students_on_floor(floor)

    return render(
        request,
        "inspections/evening_floor.html",
        {
            "day": day,
            "floor": floor,
            "session": session,
            "rows": [(s, results.get(s.pk)) for s in students],
            "statuses": StatusType.objects.active(),
            "progress": evening_session_progress(session),
            "lock_error": lock_error,
            "can_edit": request.user.has_capability(Capability.EDIT_EVENING_CHECK)
            and session.is_open
            and lock_error is None,
            "can_reopen": request.user.has_capability(Capability.REOPEN_EVENING_CHECK),
        },
    )


@login_required
@require_POST
@require_module(ModuleKey.EVENING_CHECK)
def evening_save_result(request, session_id, student_id):
    session = get_object_or_404(EveningCheckSession, pk=session_id)
    student = get_object_or_404(students_on_floor(session.floor), pk=student_id)
    try:
        result = save_evening_result(
            session=session,
            student=student,
            status=request.POST.get("status", ""),
            actor=request.user,
            note=request.POST.get("note", ""),
        )
    except (PermissionDenied, ValidationError) as exc:
        if request.headers.get("X-Partial"):
            return render(
                request,
                "inspections/_evening_row.html",
                {
                    "student": student,
                    "result": session.results.filter(student=student).first(),
                    "session": session,
                    "statuses": StatusType.objects.active(),
                    "can_edit": True,
                    "row_error": _msg(exc),
                },
                status=400,
            )
        messages.error(request, _msg(exc))
        return redirect("inspections:evening_floor", date=session.date, floor=session.floor)

    if request.headers.get("X-Partial"):
        return render(
            request,
            "inspections/_evening_row.html",
            {
                "student": student,
                "result": result,
                "session": session,
                "statuses": StatusType.objects.active(),
                "can_edit": True,
            },
        )
    return redirect("inspections:evening_floor", date=session.date, floor=session.floor)


@login_required
@require_POST
@require_module(ModuleKey.EVENING_CHECK)
def evening_close(request, session_id):
    session = get_object_or_404(EveningCheckSession, pk=session_id)
    try:
        close_evening_session(
            session=session,
            actor=request.user,
            allow_incomplete=bool(request.POST.get("allow_incomplete")),
        )
        messages.success(request, "Az ellenőrzés lezárva.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect("inspections:evening_floor", date=session.date, floor=session.floor)


@login_required
@require_POST
@require_module(ModuleKey.EVENING_CHECK)
def evening_reopen(request, session_id):
    session = get_object_or_404(EveningCheckSession, pk=session_id)
    try:
        reopen_evening_session(
            session=session, actor=request.user, reason=request.POST.get("reason", "")
        )
        messages.success(request, "Az ellenőrzés újranyitva.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect("inspections:evening_floor", date=session.date, floor=session.floor)


@login_required
@require_POST
@require_module(ModuleKey.EVENING_CHECK)
def evening_release_lock(request, session_id):
    session = get_object_or_404(EveningCheckSession, pk=session_id)
    release_session_lock(session=session, actor=request.user)
    return redirect("inspections:evening_index")


# --------------------------------------------------------------------------
# Morning round: room condition + resident status on one screen
# --------------------------------------------------------------------------


@login_required
@require_module(ModuleKey.ROOM_CHECKS)
@require_capabilities(Capability.VIEW_ROOM_CHECKS)
def roomcheck_index(request):
    date = _parse_date(request.GET.get("date"))
    sessions = {s.floor: s for s in RoomCheckSession.objects.filter(date=date)}
    can_edit = request.user.has_capability(Capability.EDIT_ROOM_CHECKS)
    rows = []
    for floor in floors():
        session = sessions.get(floor)
        if session is not None and can_edit:
            # Same back-fill as the floor page: a room added after the
            # session was opened otherwise never counts here either.
            sync_room_check_rows(session)
        rows.append(
            {
                "floor": floor,
                "session": session,
                "progress": room_check_progress(session) if session else None,
            }
        )
    return render(
        request,
        "inspections/roomcheck_index.html",
        {
            "date": date,
            "rows": rows,
            "recent": RoomCheckSession.objects.all()[:20],
            "can_edit": can_edit,
        },
    )


@login_required
@require_module(ModuleKey.ROOM_CHECKS)
@require_capabilities(Capability.VIEW_ROOM_CHECKS)
def roomcheck_floor(request, date, floor):
    day = _parse_date(date)
    session = RoomCheckSession.objects.filter(date=day, floor=floor).first()
    if session is None:
        if not request.user.has_capability(Capability.EDIT_ROOM_CHECKS):
            messages.info(request, "Erre a napra még nem indult reggeli ellenőrzés.")
            return redirect("inspections:roomcheck_index")
        session = open_room_check_session(date=day, floor=floor, actor=request.user)
    elif request.user.has_capability(Capability.EDIT_ROOM_CHECKS):
        # A room can be added to the floor after the session already exists;
        # back-fill it here too, not just at session creation, or it never
        # gets a row to rate against (see sync_room_check_rows).
        sync_room_check_rows(session)

    checks = session.checks.select_related("room", "checked_by").prefetch_related(
        "student_results__student", "student_results__status"
    )
    students_by_room = {}
    for student in students_on_floor(floor):
        room = student.current_room
        if room:
            students_by_room.setdefault(room.pk, []).append(student)

    # Pair each room with its residents and any status already recorded, so the
    # template only has to iterate.
    rows = []
    for check in checks:
        results = {r.student_id: r for r in check.student_results.all()}
        rows.append(
            {
                "check": check,
                "students": [
                    (student, results.get(student.pk))
                    for student in students_by_room.get(check.room_id, [])
                ],
            }
        )

    return render(
        request,
        "inspections/roomcheck_floor.html",
        {
            "session": session,
            "date": day,
            "floor": floor,
            "rows": rows,
            "statuses": StatusType.objects.active(),
            "progress": room_check_progress(session),
            "rating_range": range(RoomCheck.RATING_MIN, RoomCheck.RATING_MAX + 1),
            "can_edit": request.user.has_capability(Capability.EDIT_ROOM_CHECKS)
            and session.is_open,
            "can_reopen": request.user.has_capability(Capability.REOPEN_ROOM_CHECKS),
        },
    )


@login_required
@require_POST
@require_module(ModuleKey.ROOM_CHECKS)
def roomcheck_save(request, check_id):
    check = get_object_or_404(RoomCheck.objects.select_related("session", "room"), pk=check_id)
    try:
        save_room_check(
            room_check=check,
            actor=request.user,
            rating=request.POST.get("rating") or None,
            problems=request.POST.get("problems"),
            notes=request.POST.get("notes"),
        )
        messages.success(request, f"{check.room.number} mentve.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect(
        "inspections:roomcheck_floor", date=check.session.date, floor=check.session.floor
    )


@login_required
@require_POST
@require_module(ModuleKey.ROOM_CHECKS)
def roomcheck_save_student(request, check_id, student_id):
    """Set one resident's morning status from the room round."""
    check = get_object_or_404(RoomCheck.objects.select_related("session"), pk=check_id)
    student = get_object_or_404(students_on_floor(check.session.floor), pk=student_id)
    try:
        result = save_student_morning_status(
            room_check=check,
            student=student,
            actor=request.user,
            status=request.POST.get("status", ""),
            note=request.POST.get("note", ""),
        )
    except (PermissionDenied, ValidationError) as exc:
        if request.headers.get("X-Partial"):
            return render(
                request,
                "inspections/_roomcheck_student_row.html",
                {
                    "student": student,
                    "result": check.student_results.filter(student=student).first(),
                    "check": check,
                    "statuses": StatusType.objects.active(),
                    "can_edit": True,
                    "row_error": _msg(exc),
                },
                status=400,
            )
        messages.error(request, _msg(exc))
        return redirect(
            "inspections:roomcheck_floor", date=check.session.date, floor=check.session.floor
        )

    if request.headers.get("X-Partial"):
        return render(
            request,
            "inspections/_roomcheck_student_row.html",
            {
                "student": student,
                "result": result,
                "check": check,
                "statuses": StatusType.objects.active(),
                "can_edit": True,
            },
        )
    return redirect(
        "inspections:roomcheck_floor", date=check.session.date, floor=check.session.floor
    )


@login_required
@require_POST
@require_module(ModuleKey.ROOM_CHECKS)
def roomcheck_close(request, session_id):
    session = get_object_or_404(RoomCheckSession, pk=session_id)
    try:
        close_room_check_session(
            session=session,
            actor=request.user,
            allow_incomplete=bool(request.POST.get("allow_incomplete")),
        )
        messages.success(request, "A reggeli ellenőrzés lezárva.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect("inspections:roomcheck_floor", date=session.date, floor=session.floor)


@login_required
@require_POST
@require_module(ModuleKey.ROOM_CHECKS)
def roomcheck_reopen(request, session_id):
    session = get_object_or_404(RoomCheckSession, pk=session_id)
    try:
        reopen_room_check_session(
            session=session, actor=request.user, reason=request.POST.get("reason", "")
        )
        messages.success(request, "A reggeli ellenőrzés újranyitva.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect("inspections:roomcheck_floor", date=session.date, floor=session.floor)


@login_required
@require_module(ModuleKey.ROOM_CHECKS)
@require_capabilities(Capability.VIEW_ROOM_CHECK_HISTORY)
def roomcheck_history(request):
    checks = room_check_queryset_for_user(request.user).prefetch_related("history_entries")[:200]
    return render(request, "inspections/roomcheck_history.html", {"checks": checks})


@login_required
@require_module(ModuleKey.ROOM_CHECKS)
@require_capabilities(Capability.VIEW_OWN_ROOM_CHECKS)
def my_room_checks(request):
    """A student's view of their own room's inspections."""
    checks = room_check_queryset_for_user(request.user).order_by("-session__date")[:30]
    profile = getattr(request.user, "student_profile", None)
    return render(
        request,
        "inspections/my_room_checks.html",
        {"checks": checks, "room": profile.current_room if profile else None},
    )

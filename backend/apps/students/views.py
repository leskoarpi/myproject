from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.capabilities import Capability
from apps.accounts.permissions import require_capabilities
from apps.people.models import Group
from apps.presence.services import ensure_presence_row
from apps.rooms.models import Room
from apps.rooms.services import assign_student_to_room, end_room_assignment

from .forms import StudentChangeRequestForm, StudentCreateForm, StudentForm
from .models import ChangeRequestStatus, StudentChangeRequest
from .selectors import (
    can_view_sensitive,
    change_request_queryset_for_user,
    editable_fields_for,
    student_queryset_for_user,
)
from .services import (
    approve_student_change_request,
    archive_student,
    reactivate_student,
    reject_student_change_request,
    submit_student_change_request,
    update_student,
)


def _msg(exc):
    return " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


@login_required
@require_capabilities(Capability.VIEW_STUDENTS)
def student_list(request):
    show_archived = request.GET.get("archived") == "1"
    students = student_queryset_for_user(request.user, include_archived=show_archived)
    if show_archived:
        students = students.archived()

    search = request.GET.get("q", "").strip()
    if search:
        students = students.filter(
            Q(full_name__icontains=search)
            | Q(school_class__icontains=search)
            | Q(room_assignments__is_active=True, room_assignments__room__number__icontains=search)
        ).distinct()

    group_id = request.GET.get("group", "")
    if group_id.isdigit():
        students = students.filter(group_id=int(group_id))

    students = students.prefetch_related("room_assignments__room").select_related(
        "group", "presence__status"
    )
    paginator = Paginator(students.order_by("full_name"), 50)

    return render(
        request,
        "students/list.html",
        {
            "page": paginator.get_page(request.GET.get("page")),
            "search": search,
            "groups": Group.objects.filter(is_active=True),
            "selected_group": group_id,
            "show_archived": show_archived,
            "can_create": request.user.has_capability(Capability.CREATE_STUDENTS),
        },
    )


@login_required
@require_capabilities(Capability.VIEW_STUDENTS)
def student_detail(request, student_id):
    student = get_object_or_404(
        student_queryset_for_user(request.user, include_archived=True), pk=student_id
    )
    return render(
        request,
        "students/detail.html",
        {
            "student": student,
            "presence": ensure_presence_row(student) if student.is_active else None,
            "assignments": student.room_assignments.select_related("room", "school_year"),
            "show_sensitive": can_view_sensitive(request.user, student),
            "editable_fields": editable_fields_for(request.user, student),
            "can_edit": bool(editable_fields_for(request.user, student)),
            "can_request_changes": request.user.has_capability(
                Capability.REQUEST_STUDENT_CHANGES
            ),
            "can_archive": request.user.has_capability(Capability.ARCHIVE_STUDENTS),
            "can_assign": request.user.has_capability(Capability.MANAGE_ASSIGNMENTS),
            "rooms": Room.objects.active() if request.user.has_capability(
                Capability.MANAGE_ASSIGNMENTS
            ) else [],
            "change_requests": student.change_requests.select_related("requested_by")[:10],
        },
    )


@login_required
@require_capabilities(Capability.CREATE_STUDENTS)
def student_create(request):
    if request.method == "POST":
        form = StudentCreateForm(request.POST)
        if form.is_valid():
            student = form.save(actor=request.user)
            messages.success(request, f"{student.full_name} létrehozva.")
            return redirect("students:detail", student_id=student.pk)
    else:
        form = StudentCreateForm()
    return render(request, "students/form.html", {"form": form, "creating": True})


@login_required
def student_edit(request, student_id):
    student = get_object_or_404(
        student_queryset_for_user(request.user, include_archived=True), pk=student_id
    )
    allowed = editable_fields_for(request.user, student)
    if not allowed:
        raise PermissionDenied("You may not edit this student.")

    if request.method == "POST":
        form = StudentForm(request.POST, instance=student, allowed_fields=allowed)
        if form.is_valid():
            try:
                update_student(
                    student=student,
                    changes={k: form.cleaned_data[k] for k in form.fields},
                    actor=request.user,
                )
                messages.success(request, "Mentve.")
                return redirect("students:detail", student_id=student.pk)
            except (PermissionDenied, ValidationError) as exc:
                messages.error(request, _msg(exc))
    else:
        form = StudentForm(instance=student, allowed_fields=allowed)

    return render(
        request, "students/form.html", {"form": form, "student": student, "creating": False}
    )


@login_required
@require_POST
def student_archive(request, student_id):
    student = get_object_or_404(student_queryset_for_user(request.user), pk=student_id)
    try:
        archive_student(student=student, actor=request.user, note=request.POST.get("note", ""))
        messages.success(request, f"{student.full_name} archiválva.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect("students:detail", student_id=student_id)


@login_required
@require_POST
def student_reactivate(request, student_id):
    student = get_object_or_404(
        student_queryset_for_user(request.user, include_archived=True), pk=student_id
    )
    try:
        reactivate_student(student=student, actor=request.user)
        messages.success(request, f"{student.full_name} visszaállítva.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect("students:detail", student_id=student_id)


@login_required
@require_POST
def student_assign_room(request, student_id):
    student = get_object_or_404(student_queryset_for_user(request.user), pk=student_id)
    room_id = request.POST.get("room")
    try:
        if room_id:
            room = get_object_or_404(Room, pk=room_id)
            assign_student_to_room(
                student=student,
                room=room,
                actor=request.user,
                allow_overfill=bool(request.POST.get("allow_overfill")),
            )
            messages.success(request, f"{student.full_name} beköltöztetve: {room.number}.")
        else:
            assignment = student.current_assignment
            if assignment:
                end_room_assignment(assignment=assignment, actor=request.user)
                messages.success(request, "A szobabeosztás lezárva.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect("students:detail", student_id=student_id)


# --------------------------------------------------------------------------
# Change requests (spec section 37)
# --------------------------------------------------------------------------


@login_required
def change_request_create(request, student_id):
    student = get_object_or_404(student_queryset_for_user(request.user), pk=student_id)
    if not request.user.has_capability(Capability.REQUEST_STUDENT_CHANGES):
        raise PermissionDenied("Missing capability.")

    if request.method == "POST":
        form = StudentChangeRequestForm(request.POST, student=student)
        if form.is_valid():
            changes = form.proposed_changes()
            try:
                submit_student_change_request(
                    student=student,
                    actor=request.user,
                    proposed_changes=changes,
                    reason=form.cleaned_data.get("reason", ""),
                )
                messages.success(request, "A kérelmet elküldtük jóváhagyásra.")
                return redirect("students:detail", student_id=student.pk)
            except (PermissionDenied, ValidationError) as exc:
                messages.error(request, _msg(exc))
    else:
        form = StudentChangeRequestForm(student=student)

    return render(
        request, "students/change_request_form.html", {"form": form, "student": student}
    )


@login_required
@require_capabilities(
    Capability.REVIEW_STUDENT_CHANGES, Capability.REQUEST_STUDENT_CHANGES, require_all=False
)
def change_request_list(request):
    requests_qs = change_request_queryset_for_user(request.user)
    status = request.GET.get("status", ChangeRequestStatus.PENDING)
    if status:
        requests_qs = requests_qs.filter(status=status)
    return render(
        request,
        "students/change_requests.html",
        {
            "requests": requests_qs[:200],
            "statuses": ChangeRequestStatus.choices,
            "selected_status": status,
            "can_review": request.user.has_capability(Capability.REVIEW_STUDENT_CHANGES),
        },
    )


@login_required
@require_POST
def change_request_review(request, request_id):
    change_request = get_object_or_404(
        change_request_queryset_for_user(request.user), pk=request_id
    )
    approve = request.POST.get("decision") == "approve"
    note = request.POST.get("note", "")
    try:
        if approve:
            approve_student_change_request(
                change_request=change_request, actor=request.user, note=note
            )
            messages.success(request, "Jóváhagyva és alkalmazva.")
        else:
            reject_student_change_request(
                change_request=change_request, actor=request.user, note=note
            )
            messages.success(request, "Elutasítva.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
    return redirect("students:change_requests")

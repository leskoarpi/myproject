import secrets

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.capabilities import Capability, Role
from apps.accounts.permissions import require_capabilities
from apps.audit.services import AuditAction, record_audit

from . import csv_io
from .models import Group, Teacher

User = get_user_model()
PREVIEW_SESSION_KEY = "teacher_import_preview"


def _msg(exc):
    return " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ["code", "name", "description", "is_active"]


class TeacherForm(forms.ModelForm):
    username = forms.CharField(label="Felhasználónév", max_length=150, required=False)
    email = forms.EmailField(label="E-mail cím", required=False)

    class Meta:
        model = Teacher
        fields = [
            "full_name",
            "phone",
            "primary_group",
            "groups",
            "can_manage_all_weekend_stays",
            "has_all_student_access",
            "is_active",
        ]
        widgets = {"groups": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            del self.fields["username"]
            del self.fields["email"]

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        if username and User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Ez a felhasználónév már foglalt.")
        return username


@login_required
@require_capabilities(Capability.VIEW_TEACHERS)
def teacher_list(request):
    teachers = Teacher.objects.select_related("user", "primary_group").prefetch_related("groups")
    return render(
        request,
        "people/teachers.html",
        {
            "teachers": teachers,
            "groups": Group.objects.all(),
            "can_manage": request.user.has_capability(Capability.MANAGE_TEACHERS),
            "can_import": request.user.has_capability(Capability.IMPORT_DATA),
            "can_export": request.user.has_capability(Capability.EXPORT_DATA),
            "temporary_passwords": request.session.pop("teacher_temp_passwords", None),
        },
    )


@login_required
@require_capabilities(Capability.MANAGE_TEACHERS)
def teacher_form(request, teacher_id=None):
    teacher = get_object_or_404(Teacher, pk=teacher_id) if teacher_id else None
    if request.method == "POST":
        form = TeacherForm(request.POST, instance=teacher)
        if form.is_valid():
            with transaction.atomic():
                obj = form.save(commit=False)
                temp_password = None
                if teacher is None:
                    username = form.cleaned_data.get("username")
                    email = form.cleaned_data.get("email")
                    if not username or not email:
                        messages.error(request, "Új tanárhoz felhasználónév és e-mail kell.")
                        return render(request, "people/teacher_form.html", {"form": form})
                    temp_password = secrets.token_urlsafe(12)
                    obj.user = User.objects.create_user(
                        username=username,
                        email=email,
                        password=temp_password,
                        role=Role.TEACHER,
                        must_change_password=True,
                    )
                obj.save()
                form.save_m2m()
                record_audit(
                    user=request.user,
                    action=AuditAction.CREATE if teacher is None else AuditAction.UPDATE,
                    target=obj,
                )
            if temp_password:
                request.session["teacher_temp_passwords"] = {obj.user.username: temp_password}
            messages.success(request, "Mentve.")
            return redirect("people:teachers")
    else:
        form = TeacherForm(instance=teacher)
    return render(request, "people/teacher_form.html", {"form": form, "teacher": teacher})


@login_required
@require_capabilities(Capability.MANAGE_GROUPS)
def group_form(request, group_id=None):
    group = get_object_or_404(Group, pk=group_id) if group_id else None
    if request.method == "POST":
        form = GroupForm(request.POST, instance=group)
        if form.is_valid():
            obj = form.save()
            record_audit(
                user=request.user,
                action=AuditAction.CREATE if group is None else AuditAction.UPDATE,
                target=obj,
            )
            messages.success(request, "Mentve.")
            return redirect("people:teachers")
    else:
        form = GroupForm(instance=group)
    return render(request, "people/group_form.html", {"form": form, "group": group})


@login_required
@require_capabilities(Capability.EXPORT_DATA)
def teacher_export(request):
    record_audit(
        user=request.user,
        action=AuditAction.EXPORT,
        target_type="people.Teacher",
        target_repr="Teacher CSV export",
    )
    response = HttpResponse(csv_io.export_csv(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="nevelotanarok.csv"'
    return response


@login_required
@require_capabilities(Capability.IMPORT_DATA)
def teacher_template(request):
    response = HttpResponse(csv_io.template_csv(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="nevelotanarok-sablon.csv"'
    return response


@login_required
@require_capabilities(Capability.IMPORT_DATA)
def teacher_import(request):
    if request.method == "POST" and request.FILES.get("file"):
        try:
            preview = csv_io.build_preview(request.FILES["file"])
        except ValidationError as exc:
            messages.error(request, _msg(exc))
            return redirect("people:teacher_import")
        request.session[PREVIEW_SESSION_KEY] = preview.to_session()
        return render(request, "people/import_preview.html", {"preview": preview})
    return render(request, "people/import.html", {})


@login_required
@require_POST
@require_capabilities(Capability.IMPORT_DATA)
def teacher_import_apply(request):
    rows = request.session.get(PREVIEW_SESSION_KEY)
    if not rows:
        messages.error(request, "Az import előnézet lejárt. Töltsd fel újra a fájlt.")
        return redirect("people:teacher_import")
    try:
        summary, passwords = csv_io.apply_import(
            rows=rows,
            actor=request.user,
            overwrite_user_ids=request.POST.getlist("overwrite"),
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _msg(exc))
        return redirect("people:teacher_import")

    request.session.pop(PREVIEW_SESSION_KEY, None)
    if passwords:
        request.session["teacher_temp_passwords"] = passwords
    messages.success(
        request,
        f"Import kész: {summary['created']} új, {summary['updated']} frissítve, "
        f"{summary['skipped']} kihagyva.",
    )
    return redirect("people:teachers")

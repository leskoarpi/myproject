import datetime as dt

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.middleware import get_client_ip
from apps.audit.services import AuditAction, record_audit

from .capabilities import Capability, Role, capabilities_for_role, capability_label, labelled_capabilities
from .models import LoginAttempt
from .permissions import require_capabilities
from .selectors import (
    assignable_roles_for,
    can_delete_user,
    grantable_capabilities_for,
    users_manageable_by,
)
from .services import delete_user_permanently, set_user_capabilities, set_user_role

User = get_user_model()


def _recent_failures(username, ip):
    """Failed attempts for this username or IP inside the throttle window."""
    since = timezone.now() - dt.timedelta(seconds=settings.LOGIN_RATELIMIT_WINDOW_SECONDS)
    qs = LoginAttempt.objects.filter(successful=False, created_at__gte=since)
    return qs.filter(username=username).count() if not ip else qs.filter(
        username=username, ip_address=ip
    ).count()


class ThrottledAuthenticationForm(AuthenticationForm):
    """Adds a per-username/IP attempt limit on top of Django's login form."""

    def clean(self):
        username = self.cleaned_data.get("username", "")
        ip = get_client_ip(self.request)
        if username and _recent_failures(username, ip) >= settings.LOGIN_RATELIMIT_ATTEMPTS:
            raise ValidationError(
                "Túl sok sikertelen próbálkozás. Várj néhány percet, és próbáld újra.",
                code="throttled",
            )
        return super().clean()


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = ThrottledAuthenticationForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        LoginAttempt.objects.create(
            username=form.get_user().username,
            ip_address=get_client_ip(self.request),
            successful=True,
        )
        record_audit(
            user=self.request.user, action=AuditAction.LOGIN, target=self.request.user
        )
        return response

    def form_invalid(self, form):
        username = (form.data.get("username") or "")[:150]
        if username:
            LoginAttempt.objects.create(
                username=username, ip_address=get_client_ip(self.request), successful=False
            )
            record_audit(
                action=AuditAction.LOGIN_FAILED,
                target_type="accounts.User",
                target_repr=username,
                note="failed login",
            )
        return super().form_invalid(form)


class LogoutView(auth_views.LogoutView):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            record_audit(user=request.user, action=AuditAction.LOGOUT, target=request.user)
        return super().dispatch(request, *args, **kwargs)


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    form_class = PasswordChangeForm
    success_url = reverse_lazy("portal:dashboard")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["forced"] = self.request.user.must_change_password
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user
        if user.must_change_password:
            user.must_change_password = False
            user.save(update_fields=["must_change_password"])
        record_audit(user=user, action=AuditAction.PASSWORD_CHANGE, target=user)
        messages.success(self.request, "A jelszavad megváltozott.")
        return response


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/password_reset_email.txt"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


@login_required
def profile(request):
    user = request.user
    context = {
        "profile_user": user,
        "student": getattr(user, "student_profile", None),
        "teacher": getattr(user, "teacher_profile", None),
        "capabilities": labelled_capabilities(user),
    }
    return render(request, "accounts/profile.html", context)


# --------------------------------------------------------------------------
# User / privilege management (spec section 57-58)
# --------------------------------------------------------------------------


@login_required
@require_capabilities(Capability.MANAGE_USERS)
def user_list(request):
    users = users_manageable_by(request.user).select_related(
        "student_profile", "teacher_profile"
    )

    search = request.GET.get("q", "").strip()
    if search:
        users = users.filter(
            Q(username__icontains=search)
            | Q(email__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
        )
    role = request.GET.get("role", "")
    if role:
        users = users.filter(role=role)

    paginator = Paginator(users.order_by("last_name", "first_name", "username"), 50)
    return render(
        request,
        "accounts/user_list.html",
        {
            "page": paginator.get_page(request.GET.get("page")),
            "search": search,
            "roles": Role.choices,
            "selected_role": role,
        },
    )


@login_required
@require_capabilities(Capability.MANAGE_USERS)
def user_edit(request, user_id):
    target = get_object_or_404(users_manageable_by(request.user), pk=user_id)
    grantable = grantable_capabilities_for(request.user)
    role_choices = assignable_roles_for(request.user)

    if request.method == "POST":
        role = request.POST.get("role", target.role)
        extra = set(request.POST.getlist("extra"))
        revoked = set(request.POST.getlist("revoked"))
        try:
            set_user_role(target=target, actor=request.user, role=role)
            set_user_capabilities(
                target=target, actor=request.user, extra=extra, revoked=revoked
            )
            messages.success(request, f"{target.display_name} jogosultságai frissítve.")
            return redirect("accounts:user_edit", user_id=target.pk)
        except (PermissionDenied, ValidationError) as exc:
            message = " ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            messages.error(request, message)

    target.refresh_capabilities()
    role_defaults = capabilities_for_role(target.role)
    extra_current = set(target.extra_capabilities or [])
    revoked_current = set(target.revoked_capabilities or [])

    capability_rows = [
        {
            "code": code,
            "label": capability_label(code),
            "from_role": code in role_defaults,
            "is_extra": code in extra_current,
            "is_revoked": code in revoked_current,
            "effective": (code in role_defaults or code in extra_current)
            and code not in revoked_current,
        }
        for code in sorted(grantable, key=capability_label)
    ]

    return render(
        request,
        "accounts/user_edit.html",
        {
            "target": target,
            "role_choices": role_choices,
            "capability_rows": capability_rows,
            "effective_capabilities": labelled_capabilities(target),
            "can_delete": can_delete_user(request.user, target),
        },
    )


@login_required
@require_POST
@require_capabilities(Capability.MANAGE_USERS)
def user_toggle_active(request, user_id):
    target = get_object_or_404(users_manageable_by(request.user), pk=user_id)
    before = target.is_active
    target.is_active = not before
    target.save(update_fields=["is_active"])
    record_audit(
        user=request.user,
        action=AuditAction.UPDATE,
        target=target,
        old_value={"is_active": before},
        new_value={"is_active": target.is_active},
        note="account enabled" if target.is_active else "account disabled",
    )
    messages.success(
        request,
        f"{target.display_name} fiókja {'aktiválva' if target.is_active else 'letiltva'}.",
    )
    return redirect("accounts:user_list")


@login_required
@require_capabilities(Capability.DELETE_USERS)
def user_delete(request, user_id):
    """Permanent deletion. A dedicated confirmation page, not just a button
    on the list - this has no undo, unlike archiving or disabling."""
    target = get_object_or_404(users_manageable_by(request.user), pk=user_id)

    if not can_delete_user(request.user, target):
        raise PermissionDenied("Ezt a fiókot nem törölheted.")

    if request.method == "POST":
        try:
            snapshot = delete_user_permanently(
                target=target,
                actor=request.user,
                confirmation_username=request.POST.get("confirmation_username", ""),
            )
            messages.success(
                request, f"{snapshot['display_name']} fiókja véglegesen törölve."
            )
            return redirect("accounts:user_list")
        except (PermissionDenied, ValidationError) as exc:
            message = " ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            messages.error(request, message)

    return render(request, "accounts/user_delete_confirm.html", {"target": target})

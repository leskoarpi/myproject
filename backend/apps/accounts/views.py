import datetime as dt

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.core.exceptions import ValidationError
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils import timezone

from apps.audit.middleware import get_client_ip
from apps.audit.services import AuditAction, record_audit

from .capabilities import labelled_capabilities
from .models import LoginAttempt


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

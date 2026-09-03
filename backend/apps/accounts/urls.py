from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("password/change/", views.PasswordChangeView.as_view(), name="password_change"),
    path("password/reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password/reset/sent/", views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path(
        "password/reset/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password/reset/complete/",
        views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    path("profile/", views.profile, name="profile"),
    path("users/", views.user_list, name="user_list"),
    path("users/<int:user_id>/edit/", views.user_edit, name="user_edit"),
    path(
        "users/<int:user_id>/toggle-active/",
        views.user_toggle_active,
        name="user_toggle_active",
    ),
    path("users/<int:user_id>/delete/", views.user_delete, name="user_delete"),
]

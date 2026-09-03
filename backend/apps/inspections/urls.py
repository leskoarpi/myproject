from django.urls import path

from . import views

app_name = "inspections"

urlpatterns = [
    # Evening
    path("evening/", views.evening_index, name="evening_index"),
    path("evening/<str:date>/floor/<int:floor>/", views.evening_floor, name="evening_floor"),
    path(
        "evening/session/<int:session_id>/student/<int:student_id>/",
        views.evening_save_result,
        name="evening_save_result",
    ),
    path("evening/session/<int:session_id>/close/", views.evening_close, name="evening_close"),
    path("evening/session/<int:session_id>/reopen/", views.evening_reopen, name="evening_reopen"),
    path(
        "evening/session/<int:session_id>/release-lock/",
        views.evening_release_lock,
        name="evening_release_lock",
    ),
    # Morning round (room condition + resident status)
    path("morning/", views.roomcheck_index, name="roomcheck_index"),
    path("morning/<str:date>/floor/<int:floor>/", views.roomcheck_floor, name="roomcheck_floor"),
    path("morning/check/<int:check_id>/save/", views.roomcheck_save, name="roomcheck_save"),
    path(
        "morning/check/<int:check_id>/student/<int:student_id>/",
        views.roomcheck_save_student,
        name="roomcheck_save_student",
    ),
    path("morning/session/<int:session_id>/close/", views.roomcheck_close, name="roomcheck_close"),
    path(
        "morning/session/<int:session_id>/reopen/", views.roomcheck_reopen, name="roomcheck_reopen"
    ),
    path("morning/history/", views.roomcheck_history, name="roomcheck_history"),
    path("morning/mine/", views.my_room_checks, name="my_room_checks"),
]

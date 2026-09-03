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
    # Morning
    path("morning/", views.morning_index, name="morning_index"),
    path("morning/generate/", views.morning_generate, name="morning_generate"),
    path("morning/<int:snapshot_id>/", views.morning_detail, name="morning_detail"),
    path("morning/item/<int:item_id>/review/", views.morning_review_item, name="morning_review"),
    path("morning/<int:snapshot_id>/close/", views.morning_close, name="morning_close"),
    path("morning/<int:snapshot_id>/reopen/", views.morning_reopen, name="morning_reopen"),
    # Room checks
    path("rooms/", views.roomcheck_index, name="roomcheck_index"),
    path("rooms/<str:date>/floor/<int:floor>/", views.roomcheck_floor, name="roomcheck_floor"),
    path("rooms/check/<int:check_id>/save/", views.roomcheck_save, name="roomcheck_save"),
    path(
        "rooms/check/<int:check_id>/student/<int:student_id>/",
        views.roomcheck_save_student,
        name="roomcheck_save_student",
    ),
    path("rooms/session/<int:session_id>/close/", views.roomcheck_close, name="roomcheck_close"),
    path(
        "rooms/session/<int:session_id>/reopen/", views.roomcheck_reopen, name="roomcheck_reopen"
    ),
    path("rooms/history/", views.roomcheck_history, name="roomcheck_history"),
    path("rooms/mine/", views.my_room_checks, name="my_room_checks"),
]

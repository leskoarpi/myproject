from django.urls import path

from . import views

app_name = "students"

urlpatterns = [
    path("", views.student_list, name="list"),
    path("new/", views.student_create, name="create"),
    path("change-requests/", views.change_request_list, name="change_requests"),
    path(
        "change-requests/<int:request_id>/review/",
        views.change_request_review,
        name="change_request_review",
    ),
    path("<int:student_id>/", views.student_detail, name="detail"),
    path("<int:student_id>/edit/", views.student_edit, name="edit"),
    path("<int:student_id>/archive/", views.student_archive, name="archive"),
    path("<int:student_id>/delete/", views.student_delete, name="delete"),
    path("<int:student_id>/reactivate/", views.student_reactivate, name="reactivate"),
    path("<int:student_id>/assign-room/", views.student_assign_room, name="assign_room"),
    path(
        "<int:student_id>/change-request/",
        views.change_request_create,
        name="change_request_create",
    ),
]

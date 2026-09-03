from django.urls import path

from . import views

app_name = "presence"

urlpatterns = [
    path("", views.current_presence, name="current"),
    path("log/", views.presence_log, name="log"),
    path("student/<int:student_id>/", views.student_presence_detail, name="student_detail"),
    path("student/<int:student_id>/set/", views.set_student_presence, name="set_status"),
    path("me/return/", views.self_return, name="self_return"),
    path("me/leave/", views.self_leave, name="self_leave"),
]

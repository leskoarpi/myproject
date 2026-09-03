from django.urls import path

from . import views

app_name = "leave"

urlpatterns = [
    path("", views.index, name="index"),
    path("history/", views.history, name="history"),
    path("mine/", views.my_permission, name="mine"),
    path("student/<int:student_id>/grant/", views.grant, name="grant"),
    path("student/<int:student_id>/revoke/", views.revoke, name="revoke"),
]

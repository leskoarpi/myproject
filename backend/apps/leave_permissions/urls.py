from django.urls import path

from . import views

app_name = "passes"

urlpatterns = [
    path("", views.index, name="index"),
    path("history/", views.history, name="history"),
    path("mine/", views.my_rule, name="mine"),
    path("student/<int:student_id>/", views.student_rule, name="student"),
    path("student/<int:student_id>/set/", views.set_rule, name="set_rule"),
]

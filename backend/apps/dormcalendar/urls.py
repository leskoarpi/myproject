from django.urls import path

from . import views

app_name = "dormcalendar"

urlpatterns = [
    path("", views.index, name="index"),
    path("generate/", views.generate, name="generate"),
    path("day/<int:day_id>/", views.update_day, name="update_day"),
]

from django.urls import path

from . import views

app_name = "people"

urlpatterns = [
    path("", views.teacher_list, name="teachers"),
    path("teachers/new/", views.teacher_form, name="teacher_create"),
    path("teachers/<int:teacher_id>/edit/", views.teacher_form, name="teacher_edit"),
    path("teachers/export/", views.teacher_export, name="teacher_export"),
    path("teachers/template/", views.teacher_template, name="teacher_template"),
    path("teachers/import/", views.teacher_import, name="teacher_import"),
    path("teachers/import/apply/", views.teacher_import_apply, name="teacher_import_apply"),
    path("groups/new/", views.group_form, name="group_create"),
    path("groups/<int:group_id>/edit/", views.group_form, name="group_edit"),
]

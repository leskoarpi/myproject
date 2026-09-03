from django.urls import path

from . import views

app_name = "rooms"

urlpatterns = [
    path("", views.room_list, name="list"),
    path("new/", views.room_form, name="create"),
    path("export/", views.room_export, name="export"),
    path("template/", views.room_template, name="template"),
    path("import/", views.room_import, name="import"),
    path("import/apply/", views.room_import_apply, name="import_apply"),
    path("<int:room_id>/", views.room_detail, name="detail"),
    path("<int:room_id>/edit/", views.room_form, name="edit"),
]

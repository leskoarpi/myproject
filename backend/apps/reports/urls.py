from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.index, name="index"),
    path("bundle/", views.export_bundle, name="export_bundle"),
    path("morning/<int:snapshot_id>/", views.morning_report, name="morning"),
    path("<str:name>/", views.report_detail, name="detail"),
    path("<str:name>/export/<str:fmt>/", views.report_export, name="export"),
]

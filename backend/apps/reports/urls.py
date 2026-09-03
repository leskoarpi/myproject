from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.index, name="index"),
    path("bundle/", views.export_bundle, name="export_bundle"),
    path("monthly/<str:name>/", views.monthly_sheet, name="monthly"),
    path("monthly/<str:name>/xlsx/", views.monthly_xlsx, name="monthly_xlsx"),
    path("<str:name>/", views.report_detail, name="detail"),
    path("<str:name>/export/<str:fmt>/", views.report_export, name="export"),
]

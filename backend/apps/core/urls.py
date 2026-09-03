from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("modules/", views.modules, name="modules"),
    path("modules/<int:module_id>/toggle/", views.toggle_module, name="toggle_module"),
    path("school-years/", views.school_years, name="school_years"),
    path("school-years/<int:year_id>/activate/", views.activate_school_year, name="activate_year"),
    path("maintenance/", views.maintenance, name="maintenance"),
    path("maintenance/reset/", views.maintenance_reset, name="maintenance_reset"),
]

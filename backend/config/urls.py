from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("apps.portal.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("presence/", include("apps.presence.urls")),
    path("checks/", include("apps.inspections.urls")),
    path("weekend/", include("apps.weekend.urls")),
    path("passes/", include("apps.leave_permissions.urls")),
    path("students/", include("apps.students.urls")),
    path("rooms/", include("apps.rooms.urls")),
    path("people/", include("apps.people.urls")),
    path("calendar/", include("apps.dormcalendar.urls")),
    path("system/", include("apps.core.urls")),
    path("audit/", include("apps.audit.urls")),
    path("reports/", include("apps.reports.urls")),
    path("django-admin/", admin.site.urls),
]

handler403 = "apps.portal.errors.permission_denied"
handler404 = "apps.portal.errors.page_not_found"
handler500 = "apps.portal.errors.server_error"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

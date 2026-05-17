from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

from . import views


def healthz(request):
    """Lightweight liveness probe for Render health checks. No DB, no template."""
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("", views.home, name="home"),
    path("rules/", views.rules, name="rules"),
    path("healthz/", healthz, name="healthz"),
    # /admin/results/... must come BEFORE Django admin so it doesn't fall
    # through to admin.site.urls.
    path("admin/results/", include("apps.scoring.admin_urls")),
    path("admin/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    path("predictions/", include("apps.predictions.urls")),
    path("", include("apps.scoring.urls")),
    path("ops/emails/", include("apps.notifications.urls")),
]

if settings.DEBUG:
    urlpatterns += [
        path("__debug__/", include("debug_toolbar.urls")),
        path("__reload__/", include("django_browser_reload.urls")),
    ]

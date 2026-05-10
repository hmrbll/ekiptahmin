from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path
from django.views.generic import TemplateView


def healthz(request):
    """Lightweight liveness probe for Render health checks. No DB, no template."""
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("", TemplateView.as_view(template_name="home.html"), name="home"),
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    path("predictions/", include("apps.predictions.urls")),
]

if settings.DEBUG:
    urlpatterns += [
        path("__debug__/", include("debug_toolbar.urls")),
        path("__reload__/", include("django_browser_reload.urls")),
    ]

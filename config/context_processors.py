"""Project-wide context processors."""

from django.conf import settings


def deployment(request):
    """Expose a server-side `is_production` flag to templates.

    Used to gate prod-only embeds (Google Tag Manager, etc.) so they
    don't fire on dev runs and pollute analytics.
    """
    return {"is_production": not settings.DEBUG}

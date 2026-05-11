from django.apps import AppConfig


class ScoringConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.scoring'

    def ready(self) -> None:
        # Import signals so the @receiver decorators register.
        from . import signals  # noqa: F401

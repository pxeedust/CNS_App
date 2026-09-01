from django.apps import AppConfig


class OutreachConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "outreach"

    def ready(self):
        # Import registers deployment checks with Django's checks framework.
        from . import checks  # noqa: F401

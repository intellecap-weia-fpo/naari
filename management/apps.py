from django.apps import AppConfig


class ManagementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "management"

    def ready(self):
        # Import components to register them
        from . import components

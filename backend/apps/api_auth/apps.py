from django.apps import AppConfig


class ApiAuthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api_auth"

    def ready(self) -> None:
        # Registers the password-change receiver.
        from apps.api_auth import sessions  # noqa: F401

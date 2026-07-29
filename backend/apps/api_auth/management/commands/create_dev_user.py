from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "Create or update a local development user for the Fabric UI."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--username", default="fabric-admin")
        parser.add_argument("--password", default="fabric-password")
        parser.add_argument(
            "--staff",
            action="store_true",
            help="Grant admin rights. Staff users can see and drive EVERY agent, "
            "so this must stay opt-in.",
        )

    def handle(self, *args: object, **options: object) -> None:
        user_model = get_user_model()
        username = str(options["username"])
        password = str(options["password"])
        staff = bool(options["staff"])

        user, _ = user_model.objects.get_or_create(username=username)
        # Only ever escalate on request: silently making every dev user a
        # superuser would hand them all of the fleet's agents.
        if staff:
            user.is_staff = True
            user.is_superuser = True

        user.set_password(password)
        user.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Development user ready: username={username} password={password} "
                f"staff={user.is_staff}"
            )
        )

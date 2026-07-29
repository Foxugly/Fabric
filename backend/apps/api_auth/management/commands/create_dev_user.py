from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "Create or update a local development user for the Fabric UI."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--username", default="fabric-admin")
        parser.add_argument("--password", default="fabric-password")

    def handle(self, *args: object, **options: object) -> None:
        user_model = get_user_model()
        username = str(options["username"])
        password = str(options["password"])

        user, created = user_model.objects.get_or_create(
            username=username,
            defaults={"is_staff": True, "is_superuser": True},
        )
        if not created:
            user.is_staff = True
            user.is_superuser = True

        user.set_password(password)
        user.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Development user ready: username={username} password={password}"
            )
        )

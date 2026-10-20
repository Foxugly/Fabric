from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.api_auth import mfa
from apps.api_auth.sessions import revoke_refresh_tokens


class Command(BaseCommand):
    help = (
        "Clear a user's TOTP second factor and recovery codes, and sign every "
        "session out. The next login enrolls MFA again (lost phone, lockout)."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("username")

    def handle(self, *args: object, **options: object) -> None:
        username = str(options["username"])
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"No user named {username!r}.")
        mfa.reset(user)
        revoked = revoke_refresh_tokens(user.pk)
        self.stdout.write(
            self.style.SUCCESS(
                f"MFA reset for {username}: {revoked} refresh token(s) revoked. "
                "The next sign-in enrolls a new authenticator."
            )
        )

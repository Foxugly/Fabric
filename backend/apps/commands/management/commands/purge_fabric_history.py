"""Daily retention purge, run by systemd `fabric-purge.timer` (no Celery here)."""

from __future__ import annotations

from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandParser

from apps.commands.retention import purge_history, retention_days


class Command(BaseCommand):
    help = (
        "Delete command output, events, approvals and messages older than "
        "FABRIC_RETENTION_DAYS, expired WebSocket tickets, and expired "
        "SimpleJWT blacklist entries (flushexpiredtokens)."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override FABRIC_RETENTION_DAYS for this run (minimum 1).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        days = options["days"]
        if days is not None and days < 1:
            days = 1
        report = purge_history(days=days)
        self.stdout.write(
            f"purged (older than {days or retention_days()} days): {report}"
        )
        # Outstanding/blacklisted refresh tokens past their expiry are dead
        # weight: SimpleJWT's own command drops them.
        call_command("flushexpiredtokens", stdout=self.stdout)

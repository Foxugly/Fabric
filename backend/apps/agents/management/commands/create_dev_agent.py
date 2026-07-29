from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.agents.models import Agent


class Command(BaseCommand):
    help = "Create a development agent and print its token"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--name", required=True)
        parser.add_argument("--description", default="")
        parser.add_argument(
            "--owner",
            default="fabric-admin",
            help="Username that may drive this agent.",
        )

    def handle(self, *args: object, **options: Any) -> None:
        user_model = get_user_model()
        try:
            owner = user_model.objects.get(username=options["owner"])
        except user_model.DoesNotExist as exc:
            raise CommandError(f"Unknown owner '{options['owner']}'") from exc

        agent = Agent.objects.create(
            name=options["name"],
            description=options["description"],
            owner=owner,
        )
        token = agent.issue_development_token()
        self.stdout.write(f"agent_id={agent.id}")
        self.stdout.write(f"development_token={token}")

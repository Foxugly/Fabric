from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.agents.models import Agent


class Command(BaseCommand):
    help = "Create a development agent and print its token"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--name", required=True)
        parser.add_argument("--description", default="")

    def handle(self, *args: object, **options: Any) -> None:
        agent = Agent.objects.create(
            name=options["name"], description=options["description"]
        )
        token = agent.issue_development_token()
        self.stdout.write(f"agent_id={agent.id}")
        self.stdout.write(f"development_token={token}")

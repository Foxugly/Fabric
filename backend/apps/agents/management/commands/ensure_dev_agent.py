from __future__ import annotations

from django.core.management.base import BaseCommand, CommandParser

from apps.agents.models import Agent


class Command(BaseCommand):
    help = "Create or update a development agent and print env-ready credentials."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--name", default="demo-agent")
        parser.add_argument("--description", default="")

    def handle(self, *args: object, **options: object) -> None:
        name = str(options["name"])
        description = str(options["description"])

        agent, _ = Agent.objects.get_or_create(
            name=name,
            defaults={"description": description},
        )
        if description and agent.description != description:
            agent.description = description
            agent.save(update_fields=["description", "updated_at"])

        token = agent.issue_development_token()
        self.stdout.write(f"FABRIC_AGENT_ID={agent.id}")
        self.stdout.write(f"FABRIC_AGENT_TOKEN={token}")

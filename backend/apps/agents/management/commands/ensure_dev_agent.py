from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.agents.models import Agent


class Command(BaseCommand):
    help = "Create or update a development agent and print env-ready credentials."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--name", default="demo-agent")
        parser.add_argument("--description", default="")
        parser.add_argument(
            "--owner",
            default="fabric-admin",
            help="Username that may drive this agent. Required: an ownerless "
            "agent is invisible to non-staff users.",
        )

    def handle(self, *args: object, **options: object) -> None:
        name = str(options["name"])
        description = str(options["description"])
        owner_username = str(options["owner"])

        user_model = get_user_model()
        try:
            owner = user_model.objects.get(username=owner_username)
        except user_model.DoesNotExist as exc:
            raise CommandError(
                f"Unknown owner '{owner_username}'. Create it first with "
                f"`manage.py create_dev_user --username {owner_username}`."
            ) from exc

        agent, _ = Agent.objects.get_or_create(
            name=name,
            defaults={"description": description, "owner": owner},
        )
        update_fields: list[str] = []
        if description and agent.description != description:
            agent.description = description
            update_fields.append("description")
        if agent.owner_id != owner.pk:
            agent.owner = owner
            update_fields.append("owner")
        if update_fields:
            agent.save(update_fields=[*update_fields, "updated_at"])

        token = agent.issue_development_token()
        self.stdout.write(f"FABRIC_AGENT_ID={agent.id}")
        self.stdout.write(f"FABRIC_AGENT_TOKEN={token}")

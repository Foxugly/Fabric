from __future__ import annotations

from urllib.parse import parse_qs

from apps.agents.models import Agent, AgentStatus


def authenticate_agent(query_string: bytes) -> Agent | None:
    params = parse_qs(query_string.decode("utf-8"))
    agent_ids = params.get("agent_id", [])
    tokens = params.get("token", [])
    if len(agent_ids) != 1 or len(tokens) != 1:
        return None
    try:
        agent = Agent.objects.get(id=agent_ids[0])
    except Agent.DoesNotExist:
        return None
    if agent.status == AgentStatus.DISABLED:
        return None
    if not agent.check_development_token(tokens[0]):
        return None
    return agent

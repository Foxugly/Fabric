from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs
from uuid import UUID

from apps.agents.models import Agent, AgentStatus


def bearer_token(headers: Any) -> str:
    """The `Authorization: Bearer <token>` value of an ASGI handshake, or ""."""
    for name, value in headers or []:
        if bytes(name).lower() != b"authorization":
            continue
        scheme, _, token = bytes(value).decode("latin-1").strip().partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
        return ""
    return ""


def authenticate_agent(scope: dict[str, Any]) -> Agent | None:
    """Authenticate the agent socket from its handshake header.

    Contract: `Authorization: Bearer <agent token>`. The token is never read
    from the URL any more — URLs end up in access logs. The agent is found by
    the token's hash; an optional non-secret `agent_id` query parameter, when
    present, must name the same agent.
    """
    raw_token = bearer_token(scope.get("headers"))
    if not raw_token:
        return None
    agent = Agent.objects.filter(
        development_token_hash=Agent.hash_token(raw_token)
    ).first()
    if agent is None:
        return None

    params = parse_qs(bytes(scope.get("query_string", b"")).decode("utf-8"))
    agent_ids = params.get("agent_id", [])
    if len(agent_ids) > 1:
        return None
    if agent_ids:
        try:
            if UUID(agent_ids[0]) != agent.id:
                return None
        except ValueError:
            return None

    if agent.status == AgentStatus.DISABLED:
        return None
    # Constant-time comparison of the full hash, as before.
    if not agent.check_development_token(raw_token):
        return None
    return agent

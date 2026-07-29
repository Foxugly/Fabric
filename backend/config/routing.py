from __future__ import annotations

from channels.security.websocket import OriginValidator
from django.conf import settings
from django.urls import path

from apps.agents.consumers import AgentConsumer, EventConsumer


def browser_allowed_origins() -> list[str]:
    """Origins allowed to open the browser event socket."""
    origins = list(getattr(settings, "CORS_ALLOWED_ORIGINS", []))
    for host in getattr(settings, "ALLOWED_HOSTS", []):
        if host in {"*", "localhost", "127.0.0.1"}:
            continue
        origins.append(f"https://{host}")
    return origins


#: Only the browser socket is origin-checked. The agent socket is opened by a
#: Python client that sends no `Origin` header at all — `OriginValidator` denies
#: those — and it authenticates with its own token in `authenticate_agent`.
websocket_urlpatterns = [
    path("ws/v1/agent/", AgentConsumer.as_asgi()),
    path(
        "ws/v1/events/",
        OriginValidator(EventConsumer.as_asgi(), browser_allowed_origins()),
    ),
]

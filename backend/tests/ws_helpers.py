"""WebSocket test helpers: agent bearer header, browser event-socket ticket."""

from __future__ import annotations

from typing import Any

from asgiref.sync import sync_to_async

from apps.api_auth.tickets import issue_ticket

#: The browser event socket is origin-checked; a real browser always sends this.
BROWSER_HEADERS = [(b"origin", b"http://127.0.0.1:4200")]


def agent_headers(token: str) -> list[tuple[bytes, bytes]]:
    """Handshake headers of the agent socket (`Authorization: Bearer …`)."""
    return [(b"authorization", f"Bearer {token}".encode())]


async def events_path(user: Any) -> str:
    """Path of the browser event socket, with a fresh single-use ticket."""
    ticket = await sync_to_async(issue_ticket)(user)
    return f"/ws/v1/events/?ticket={ticket}"

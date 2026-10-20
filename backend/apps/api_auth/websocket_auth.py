from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from apps.api_auth.tickets import consume_ticket


def authenticate_events_user(query_string: bytes) -> Any | None:
    """Authenticate the browser event socket with a single-use ticket.

    The SPA obtains the ticket from `POST /api/v1/auth/ws-ticket/` with its
    in-memory access token, then opens `/ws/v1/events/?ticket=…`. Long-lived
    credentials never appear in the URL.
    """
    params = parse_qs(query_string.decode("utf-8"))
    tickets = params.get("ticket", [])
    if len(tickets) != 1:
        return None
    return consume_ticket(tickets[0])

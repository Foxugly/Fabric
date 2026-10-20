"""Single-use tickets for the browser event socket (see `WebSocketTicket`)."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.api_auth.models import WebSocketTicket


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ticket_ttl_seconds() -> int:
    return int(getattr(settings, "FABRIC_WS_TICKET_TTL_SECONDS", 30))


def issue_ticket(user: Any) -> str:
    now = timezone.now()
    # Keep the table bounded: expired tickets are useless, whoever owns them.
    WebSocketTicket.objects.filter(expires_at__lte=now).delete()
    raw = secrets.token_urlsafe(32)
    WebSocketTicket.objects.create(
        key_hash=_hash(raw),
        user=user,
        expires_at=now + timedelta(seconds=ticket_ttl_seconds()),
    )
    return raw


def consume_ticket(raw: str) -> Any | None:
    """Return the ticket's active user, or None. A ticket works exactly once.

    The DELETE is the atomic step: of two handshakes racing on the same ticket,
    only the one whose DELETE removes the row gets the user.
    """
    if not raw:
        return None
    ticket = (
        WebSocketTicket.objects.select_related("user")
        .filter(key_hash=_hash(raw))
        .first()
    )
    if ticket is None:
        return None
    deleted, _ = WebSocketTicket.objects.filter(pk=ticket.pk).delete()
    if deleted != 1:
        return None
    if ticket.expires_at <= timezone.now():
        return None
    if not ticket.user.is_active:
        return None
    return ticket.user

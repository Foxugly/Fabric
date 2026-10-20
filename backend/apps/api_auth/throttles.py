from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.throttling import (
    AnonRateThrottle,
    SimpleRateThrottle,
    UserRateThrottle,
)


class LoginRateThrottle(AnonRateThrottle):
    """Per client IP — the last X-Forwarded-For entry (`NUM_PROXIES = 1`)."""

    scope = "login"


class LoginUsernameRateThrottle(SimpleRateThrottle):
    """Per targeted username, whatever the source IP.

    The IP throttle alone lets a botnet spread a guessing campaign over many
    addresses. Counting failures per account bounds the total, at the cost of a
    possible lockout of that account while the attack lasts — acceptable for the
    one barrier in front of remote code execution.
    """

    scope = "login_username"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        data = request.data if hasattr(request.data, "get") else {}
        username = str(data.get("username", "")).strip().lower()
        if not username:
            return None
        return self.cache_format % {"scope": self.scope, "ident": username}


class PasswordChangeRateThrottle(UserRateThrottle):
    """Per user: a stolen access token must not be a current-password oracle."""

    scope = "password_change"


class WebSocketTicketRateThrottle(UserRateThrottle):
    scope = "ws_ticket"

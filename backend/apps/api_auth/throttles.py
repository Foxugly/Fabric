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


class AccountDeleteRateThrottle(UserRateThrottle):
    """Per user: account deletion checks the password, so it is an oracle too."""

    scope = "account_delete"


class WebSocketTicketRateThrottle(UserRateThrottle):
    scope = "ws_ticket"


class MfaRateThrottle(AnonRateThrottle):
    """MFA code checks per client IP (same `NUM_PROXIES` keying as login)."""

    scope = "mfa"


class MfaUserRateThrottle(SimpleRateThrottle):
    """MFA code checks per account: the user the `mfa_token` names.

    A 6-digit code is guessable; bounding the attempts per account, whatever
    the source IP, keeps a stolen password from being enough.
    """

    scope = "mfa_user"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        from apps.api_auth.mfa import peek_token_user_id

        data = request.data if hasattr(request.data, "get") else {}
        user_id = peek_token_user_id(str(data.get("mfa_token") or ""))
        if user_id is None:
            return None
        return self.cache_format % {"scope": self.scope, "ident": user_id}


class MfaRecoveryCodesRateThrottle(UserRateThrottle):
    """Per user: regenerating the codes checks the password (an oracle)."""

    scope = "mfa_recovery_codes"

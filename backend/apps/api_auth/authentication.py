"""Token authentication with an expiry.

DRF's stock `TokenAuthentication` never expires a token. Fabric's tokens open a
socket that can run code on the operator's machine, so age matters: a token
copied out of a browser must stop working on its own.

This module is the single source of truth for the TTL — the HTTP authenticator,
the WebSocket authenticator and the login view all read it from here.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone
from rest_framework import authentication, exceptions
from rest_framework.authtoken.models import Token

DEFAULT_TTL_HOURS = 168


def token_ttl() -> timedelta:
    """Token lifetime. Zero or less disables expiry."""
    hours = int(getattr(settings, "FABRIC_TOKEN_TTL_HOURS", DEFAULT_TTL_HOURS))
    return timedelta(hours=hours)


def is_token_expired(token: Token) -> bool:
    ttl = token_ttl()
    if ttl.total_seconds() <= 0:
        return False
    return timezone.now() - token.created > ttl


class ExpiringTokenAuthentication(authentication.TokenAuthentication):
    def authenticate_credentials(self, key: str) -> tuple[Any, Token]:
        user, token = super().authenticate_credentials(key)
        if is_token_expired(token):
            # Delete rather than merely refuse: the credential is spent, and
            # leaving the row behind would make it usable again if the TTL were
            # ever raised.
            token.delete()
            raise exceptions.AuthenticationFailed("Token has expired")
        return user, token

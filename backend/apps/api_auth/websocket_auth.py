from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from rest_framework.authtoken.models import Token

from apps.api_auth.authentication import is_token_expired


def authenticate_events_user(query_string: bytes) -> Any | None:
    params = parse_qs(query_string.decode("utf-8"))
    token_value = params.get("token", [""])[0]
    if not token_value:
        return None

    try:
        token = Token.objects.select_related("user").get(key=token_value)
    except Token.DoesNotExist:
        return None

    # The socket outlives the HTTP request that opened it, so the expiry has to
    # be checked here too — otherwise a stale token still gets a live feed.
    if is_token_expired(token):
        token.delete()
        return None

    if not token.user.is_active:
        return None
    return token.user

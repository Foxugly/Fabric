"""HttpOnly refresh-token cookie for the web SPA.

Mirrors PushIT_server `accounts/refresh_cookie.py` (fleet pilot, OPERATIONS.md
§3.25). The SPA opts in by sending ``X-Auth-Mode: cookie`` on its auth calls.
For those requests the refresh token travels in an ``HttpOnly; Secure;
SameSite=Lax`` cookie scoped to ``/api/v1/auth/`` and is **omitted from the
JSON body** — JavaScript never sees it, so an XSS cannot exfiltrate it. The
access token is short-lived and kept in the SPA's memory only.

CSRF: once the cookie authenticates ``refresh/`` and ``logout/``, a cross-site
page could try to drive them. Two independent guards:

* the custom ``X-Auth-Mode`` header makes every cookie-mode call a non-simple
  CORS request, so the browser preflights it and only the explicitly allowed
  origins (``CORS_ALLOWED_ORIGINS``, credentials on) get through;
* every cookie-mode request must also carry an ``Origin`` that is in
  ``CORS_ALLOWED_ORIGINS`` (checked server-side, 403 otherwise).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from django.conf import settings
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

AUTH_MODE_HEADER = "X-Auth-Mode"
AUTH_MODE_COOKIE = "cookie"
#: Claim carried by the refresh token (and preserved by rotation) telling
#: whether the cookie outlives the browser session ("remember me").
PERSIST_CLAIM = "persist"


def cookie_name() -> str:
    return str(settings.AUTH_REFRESH_COOKIE_NAME)


def wants_cookie(request: Request) -> bool:
    """True when the client opted into cookie transport for the refresh token."""
    header = request.headers.get(AUTH_MODE_HEADER, "")
    return header.strip().lower() == AUTH_MODE_COOKIE


def origin_allowed(request: Request) -> bool:
    origin = request.headers.get("Origin")
    return bool(origin) and origin in settings.CORS_ALLOWED_ORIGINS


def origin_rejected_response() -> Response:
    return Response(
        {
            "code": "origin_not_allowed",
            "detail": "Cookie authentication is only accepted from an allowed origin.",
        },
        status=status.HTTP_403_FORBIDDEN,
    )


def cookie_origin_guard(request: Request) -> Response | None:
    """403 for a cookie-mode request from an origin that is not allowed (CSRF)."""
    if wants_cookie(request) and not origin_allowed(request):
        return origin_rejected_response()
    return None


def read_refresh_cookie(request: Request) -> str:
    return str(request.COOKIES.get(cookie_name(), ""))


def _persists(refresh: str) -> bool:
    # Only ever called on a token this process just minted or rotated, so the
    # signature was checked upstream; we read one claim, we do not trust it.
    try:
        token = RefreshToken(refresh, verify=False)  # type: ignore[arg-type]
    except TokenError:
        return True
    return token.payload.get(PERSIST_CLAIM, True) is not False


def set_refresh_cookie(response: Response, refresh: str) -> None:
    max_age = None
    if _persists(refresh):
        lifetime = cast(timedelta, settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"])
        max_age = int(lifetime.total_seconds())
    response.set_cookie(
        cookie_name(),
        refresh,
        max_age=max_age,
        path=settings.AUTH_REFRESH_COOKIE_PATH,
        domain=None,  # host-only: never the whole foxugly.com
        secure=settings.AUTH_REFRESH_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        cookie_name(),
        path=settings.AUTH_REFRESH_COOKIE_PATH,
        samesite="Lax",
    )


def deliver_tokens(request: Request, response: Response) -> Response:
    """Move ``refresh`` from the JSON body into the cookie when opted in.

    No-op for body-mode clients (scripts calling the API directly).
    """
    if not wants_cookie(request):
        return response
    data: Any = getattr(response, "data", None)
    if isinstance(data, dict) and data.get("refresh"):
        refresh = data.pop("refresh")
        set_refresh_cookie(response, refresh)
    return response

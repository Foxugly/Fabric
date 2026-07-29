"""Liveness endpoint for nginx, UptimeRobot and the deploy pipeline.

Deliberately outside `/api/v1/` (OPERATIONS.md §3.18: `/health/` is not an API)
and unauthenticated, but it never leaks anything beyond component status.
"""

from __future__ import annotations

from typing import Any

from django.db import connection
from django.http import HttpRequest, JsonResponse


def health(request: HttpRequest) -> JsonResponse:
    checks: dict[str, Any] = {"database": _check_database()}
    checks["channel_layer"] = _check_channel_layer()

    healthy = all(value == "ok" for value in checks.values())
    return JsonResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status=200 if healthy else 503,
    )


def _check_database() -> str:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return f"error: {exc.__class__.__name__}"
    return "ok"


def _check_channel_layer() -> str:
    """A dead channel layer means commands never reach any agent."""
    try:
        from channels.layers import get_channel_layer

        if get_channel_layer() is None:
            return "error: not configured"
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return f"error: {exc.__class__.__name__}"
    return "ok"

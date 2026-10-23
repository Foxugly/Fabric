"""Liveness endpoint for nginx, UptimeRobot and the deploy pipeline.

Deliberately outside `/api/v1/` (OPERATIONS.md §3.18: `/health/` is not an API)
and unauthenticated, but it never leaks anything beyond component status.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import connection
from django.http import HttpRequest, JsonResponse

CHANNEL_LAYER_TIMEOUT_SECONDS = 3

LOGGER = logging.getLogger("apps.health")

#: The only value a failed check ever reports. Exception class names and
#: messages stay in the server log: an unauthenticated probe must not learn
#: which driver, host or library failed.
CHECK_ERROR = "error"


def health(request: HttpRequest) -> JsonResponse:
    checks: dict[str, Any] = {
        "database": _check_database(),
        "channel_layer": _check_channel_layer(),
    }

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
    except Exception:  # noqa: BLE001 - logged, never raised nor exposed
        LOGGER.exception("health: database check failed")
        return CHECK_ERROR
    return "ok"


def _check_channel_layer() -> str:
    """Round-trip a message through the layer, not just read the setting.

    Checking only that a layer is configured is what let a broken Redis client
    reach production: `/health/` said ok while every WebSocket died with a 1011.
    A real send/receive is the only assertion worth making here.
    """
    try:
        channel_layer = get_channel_layer()
        if channel_layer is None:
            LOGGER.error("health: no channel layer configured")
            return CHECK_ERROR
        async_to_sync(_round_trip)(channel_layer)
    except TimeoutError:
        LOGGER.error("health: channel layer round trip timed out")
        return CHECK_ERROR
    except Exception:  # noqa: BLE001 - logged, never raised nor exposed
        LOGGER.exception("health: channel layer check failed")
        return CHECK_ERROR
    return "ok"


async def _round_trip(channel_layer: Any) -> None:
    channel = f"fabric.health.{uuid.uuid4().hex}"
    await asyncio.wait_for(
        channel_layer.send(channel, {"type": "health.ping"}),
        timeout=CHANNEL_LAYER_TIMEOUT_SECONDS,
    )
    await asyncio.wait_for(
        channel_layer.receive(channel),
        timeout=CHANNEL_LAYER_TIMEOUT_SECONDS,
    )

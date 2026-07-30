"""`/health/` must exercise its dependencies, not merely read settings.

A version of this endpoint that only checked `get_channel_layer() is not None`
reported "ok" in production while every WebSocket died on a broken Redis client.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_health_is_public_and_reports_ok() -> None:
    response = APIClient().get("/health/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"] == {"database": "ok", "channel_layer": "ok"}


@pytest.mark.django_db
def test_health_degrades_when_the_channel_layer_is_unusable() -> None:
    with patch("config.health._round_trip", side_effect=TimeoutError):
        response = APIClient().get("/health/")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["checks"]["channel_layer"] == "error: timeout"
    assert payload["checks"]["database"] == "ok"


@pytest.mark.django_db
def test_health_degrades_when_the_database_is_unusable() -> None:
    with patch("config.health._check_database", return_value="error: OperationalError"):
        response = APIClient().get("/health/")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "error: OperationalError"


@pytest.mark.django_db
def test_health_really_round_trips_the_channel_layer() -> None:
    """Guard the regression directly: the check must touch the layer."""
    calls: list[Any] = []
    original = None

    from config import health

    original = health._round_trip

    async def spy(channel_layer: Any) -> None:
        calls.append(channel_layer)
        await original(channel_layer)

    with patch.object(health, "_round_trip", spy):
        response = APIClient().get("/health/")

    assert response.status_code == 200
    assert len(calls) == 1


def test_sentry_needs_more_than_a_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A production DSN in a developer's .env must not start reporting.

    This is the pushit-backend failure mode: 31 dev-machine crashes landed in the
    production project because the presence of a DSN was the only condition.
    """
    import importlib

    def resolve(**env: str) -> bool:
        for key in ("STATE", "SENTRY_DSN", "SENTRY_ENABLE", "DEBUG", "SECRET_KEY"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        module = importlib.reload(importlib.import_module("config.settings"))
        return bool(module.SENTRY_ENABLED)

    dsn = "https://k@o1.ingest.de.sentry.io/2"
    try:
        assert resolve(STATE="DEV", SENTRY_DSN=dsn) is False
        assert resolve(STATE="PROD", SENTRY_DSN=dsn, SECRET_KEY="x" * 40,
                       REDIS_URL="redis://127.0.0.1:6379/9") is True
        assert resolve(STATE="DEV", SENTRY_DSN=dsn, SENTRY_ENABLE="true") is True
        assert resolve(STATE="PROD", SECRET_KEY="x" * 40,
                       REDIS_URL="redis://127.0.0.1:6379/9") is False
    finally:
        # Leave the module as the rest of the suite expects it.
        importlib.reload(importlib.import_module("config.settings"))

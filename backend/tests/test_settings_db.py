"""Database settings that must hold under daphne (ASGI)."""

from __future__ import annotations

import importlib

import pytest


def test_no_persistent_connections_under_daphne(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M5: CONN_MAX_AGE is gunicorn-only (§3.22); ASGI threads would leak them."""
    monkeypatch.setenv("DB_ENGINE", "postgresql")
    try:
        module = importlib.reload(importlib.import_module("config.settings"))
        database = module.DATABASES["default"]
        assert database["ENGINE"] == "django.db.backends.postgresql"
        assert database["CONN_MAX_AGE"] == 0
    finally:
        monkeypatch.undo()
        importlib.reload(importlib.import_module("config.settings"))

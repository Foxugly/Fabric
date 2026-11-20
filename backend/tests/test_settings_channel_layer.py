"""The Redis channel layer must not inherit redis-py 8's 5 s socket read timeout."""

from __future__ import annotations

import importlib

import pytest
from channels_redis.core import RedisChannelLayer
from channels_redis.utils import create_pool


def test_channel_layer_has_no_socket_read_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """channels-redis blocks on BZPOPMIN for 5 s; redis-py 8 gave up at 5 s too.

    The client then raised "Timeout reading from ..." and every idle WebSocket
    died with a 1011 (production, 2026-07-30).
    """
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/5")
    try:
        module = importlib.reload(importlib.import_module("config.settings"))
        layer = RedisChannelLayer(**module.CHANNEL_LAYERS["default"]["CONFIG"])
        pool = create_pool(layer.hosts[0])
        assert pool.connection_kwargs.get("socket_timeout", "unset") is None
    finally:
        monkeypatch.undo()
        importlib.reload(importlib.import_module("config.settings"))

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))


@pytest.fixture(autouse=True)
def _channel_layer_settings(settings: Any) -> None:
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }


@pytest.fixture(autouse=True)
def _clear_throttle_state() -> Any:
    # DRF throttling counts in the cache, and every caller is throttled now:
    # leaking counters across tests would make them order-dependent.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_user(db: Any) -> Any:
    user_model = get_user_model()
    return user_model.objects.create_user(
        username="fabric-admin",
        password="fabric-password",
        is_staff=True,
    )


@pytest.fixture
def authenticated_api_client(api_user: Any) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=api_user)
    return client

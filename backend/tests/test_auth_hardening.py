"""Login is the only barrier between the Internet and RCE on the operator's PC."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

CREDENTIALS = {"username": "hardening-user", "password": "a-long-enough-password"}


@pytest.fixture
def user(db: Any) -> Any:
    return get_user_model().objects.create_user(**CREDENTIALS)


@pytest.fixture(autouse=True)
def _clear_throttle_state() -> Any:
    # DRF throttling counts in the cache; leaking counters across tests would
    # make them order-dependent.
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_login_returns_a_token(user: Any) -> None:
    response = APIClient().post("/api/v1/auth/login/", CREDENTIALS, format="json")

    assert response.status_code == 200
    assert response.json()["token"]


@pytest.mark.django_db
def test_logging_in_twice_keeps_the_same_token(user: Any) -> None:
    """Signing in on a phone must not sign the desktop out."""
    client = APIClient()

    first = client.post("/api/v1/auth/login/", CREDENTIALS, format="json")
    second = client.post("/api/v1/auth/login/", CREDENTIALS, format="json")

    assert first.json()["token"] == second.json()["token"]


@pytest.mark.django_db
def test_brute_force_is_rate_limited(user: Any) -> None:
    """Exercise the configured rate rather than overriding it.

    DRF freezes `THROTTLE_RATES` as a class attribute when
    `rest_framework.throttling` is imported, so overriding
    `DEFAULT_THROTTLE_RATES` from a test only works when this module happens to
    run first — which made this test pass alone and fail in the suite.
    """
    rates = cast(dict[str, str], settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
    limit = int(rates["login"].split("/")[0])
    client = APIClient()
    wrong = {**CREDENTIALS, "password": "wrong-password"}

    statuses = [
        client.post("/api/v1/auth/login/", wrong, format="json").status_code
        for _ in range(limit + 2)
    ]

    assert statuses[:limit] == [400] * limit, statuses
    assert statuses[limit:] == [429, 429], statuses


@pytest.mark.django_db
def test_an_expired_token_is_refused_and_deleted(user: Any) -> None:
    token = Token.objects.create(user=user)
    Token.objects.filter(pk=token.pk).update(
        created=timezone.now() - timedelta(days=365)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    response = client.get("/api/v1/agents/")

    assert response.status_code == 401
    assert not Token.objects.filter(pk=token.pk).exists()


@pytest.mark.django_db
def test_a_fresh_token_still_works(user: Any) -> None:
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    assert client.get("/api/v1/agents/").status_code == 200


@pytest.mark.django_db
def test_logging_in_replaces_an_expired_token(user: Any) -> None:
    stale = Token.objects.create(user=user)
    Token.objects.filter(pk=stale.pk).update(
        created=timezone.now() - timedelta(days=365)
    )

    response = APIClient().post("/api/v1/auth/login/", CREDENTIALS, format="json")

    assert response.status_code == 200
    assert response.json()["token"] != stale.key


@pytest.mark.django_db
def test_ttl_of_zero_disables_expiry(user: Any, settings: Any) -> None:
    settings.FABRIC_TOKEN_TTL_HOURS = 0
    token = Token.objects.create(user=user)
    Token.objects.filter(pk=token.pk).update(
        created=timezone.now() - timedelta(days=365)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    assert client.get("/api/v1/agents/").status_code == 200

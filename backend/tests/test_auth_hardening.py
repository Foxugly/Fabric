"""Login is the only barrier between the Internet and RCE on the operator's PC."""

from __future__ import annotations

from typing import Any, cast

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

CREDENTIALS = {"username": "hardening-user", "password": "a-long-enough-password"}
ORIGIN = "http://127.0.0.1:4200"
COOKIE = "fabric_refresh"


@pytest.fixture
def user(db: Any) -> Any:
    return get_user_model().objects.create_user(**CREDENTIALS)


def _rate(scope: str) -> int:
    """The configured limit. DRF freezes `THROTTLE_RATES` at import time, so the
    tests exercise the configured rate rather than overriding it."""
    rates = cast(dict[str, str], settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
    return int(rates[scope].split("/")[0])


def _cookie_client() -> APIClient:
    client = APIClient()
    client.credentials(HTTP_X_AUTH_MODE="cookie", HTTP_ORIGIN=ORIGIN)
    return client


# --- B2: throttles -----------------------------------------------------------


@pytest.mark.django_db
def test_brute_force_is_rate_limited(user: Any) -> None:
    limit = _rate("login")
    client = APIClient()
    wrong = {**CREDENTIALS, "password": "wrong-password"}

    statuses = [
        client.post("/api/v1/auth/login/", wrong, format="json").status_code
        for _ in range(limit + 2)
    ]

    assert statuses[:limit] == [400] * limit, statuses
    assert statuses[limit:] == [429, 429], statuses


@pytest.mark.django_db
def test_forged_x_forwarded_for_prefix_does_not_reset_the_login_throttle(
    user: Any,
) -> None:
    """nginx appends the real peer; only that LAST entry may identify the client.

    Before `NUM_PROXIES = 1`, DRF keyed the throttle on the whole header, so a
    different forged prefix per request made the limit meaningless.
    """
    limit = _rate("login")
    client = APIClient()
    wrong = {**CREDENTIALS, "password": "wrong-password"}

    statuses = [
        client.post(
            "/api/v1/auth/login/",
            wrong,
            format="json",
            HTTP_X_FORWARDED_FOR=f"10.66.{i}.1, 203.0.113.7",
        ).status_code
        for i in range(limit + 1)
    ]

    assert statuses[-1] == 429, statuses


@pytest.mark.django_db
def test_login_is_also_throttled_per_username(user: Any) -> None:
    """A campaign spread over many IPs still hits the per-account bound."""
    limit = _rate("login_username")
    client = APIClient()
    wrong = {**CREDENTIALS, "password": "wrong-password"}

    statuses = [
        client.post(
            "/api/v1/auth/login/",
            wrong,
            format="json",
            HTTP_X_FORWARDED_FOR=f"198.51.100.{i}",
        ).status_code
        for i in range(limit + 1)
    ]

    assert statuses[:limit] == [400] * limit, statuses
    assert statuses[-1] == 429, statuses


@pytest.mark.django_db
def test_authenticated_users_are_throttled_too(user: Any) -> None:
    """M4: the default throttle classes cover signed-in users."""
    from rest_framework.settings import api_settings
    from rest_framework.throttling import UserRateThrottle

    classes: list[Any] = list(api_settings.DEFAULT_THROTTLE_CLASSES)
    assert UserRateThrottle in classes
    assert _rate("user") > 0


# --- I3: JWT + HttpOnly refresh cookie ---------------------------------------


@pytest.mark.django_db
def test_cookie_mode_login_sets_an_httponly_cookie_and_hides_the_refresh(
    user: Any,
) -> None:
    response = _cookie_client().post("/api/v1/auth/login/", CREDENTIALS, format="json")

    assert response.status_code == 200
    body = response.json()
    assert body["access"]
    assert "refresh" not in body
    cookie = response.cookies[COOKIE]
    assert cookie["httponly"]
    assert cookie["path"] == "/api/v1/auth/"
    assert cookie["samesite"] == "Lax"
    # No "remember me": a browser-session cookie.
    assert cookie["max-age"] == ""


@pytest.mark.django_db
def test_cookie_mode_refresh_rotates_the_cookie(user: Any) -> None:
    client = _cookie_client()
    login = client.post("/api/v1/auth/login/", CREDENTIALS, format="json")
    first = login.cookies[COOKIE].value

    refreshed = client.post("/api/v1/auth/refresh/", {}, format="json")

    assert refreshed.status_code == 200
    assert refreshed.json()["access"]
    assert "refresh" not in refreshed.json()
    assert refreshed.cookies[COOKIE].value != first
    # The rotated-out token is blacklisted: replaying it fails.
    replay = APIClient().post(
        "/api/v1/auth/refresh/", {"refresh": first}, format="json"
    )
    assert replay.status_code == 401


@pytest.mark.django_db
def test_cookie_mode_refresh_without_cookie_is_401(user: Any) -> None:
    response = _cookie_client().post("/api/v1/auth/refresh/", {}, format="json")

    assert response.status_code == 401
    assert response.json()["code"] == "refresh_cookie_missing"


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/v1/auth/refresh/", "/api/v1/auth/logout/"])
def test_cookie_mode_refuses_a_foreign_origin(user: Any, path: str) -> None:
    client = _cookie_client()
    client.post("/api/v1/auth/login/", CREDENTIALS, format="json")
    client.credentials(HTTP_X_AUTH_MODE="cookie", HTTP_ORIGIN="https://evil.example")

    response = client.post(path, {}, format="json")

    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"


@pytest.mark.django_db
def test_cookie_mode_refuses_a_missing_origin(user: Any) -> None:
    client = APIClient()
    client.credentials(HTTP_X_AUTH_MODE="cookie")

    response = client.post("/api/v1/auth/login/", CREDENTIALS, format="json")

    assert response.status_code == 403


@pytest.mark.django_db
def test_cookie_mode_logout_blacklists_and_clears_the_cookie(user: Any) -> None:
    client = _cookie_client()
    login = client.post("/api/v1/auth/login/", CREDENTIALS, format="json")
    refresh = login.cookies[COOKIE].value

    response = client.post("/api/v1/auth/logout/", {}, format="json")

    assert response.status_code == 204
    assert response.cookies[COOKIE].value == ""
    replay = APIClient().post(
        "/api/v1/auth/refresh/", {"refresh": refresh}, format="json"
    )
    assert replay.status_code == 401


@pytest.mark.django_db
def test_access_token_authenticates_the_api(user: Any) -> None:
    access = (
        APIClient()
        .post("/api/v1/auth/login/", CREDENTIALS, format="json")
        .json()["access"]
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    assert client.get("/api/v1/auth/me/").status_code == 200


@pytest.mark.django_db
def test_password_change_revokes_every_refresh_token(user: Any) -> None:
    other_session = (
        APIClient()
        .post("/api/v1/auth/login/", CREDENTIALS, format="json")
        .json()["refresh"]
    )
    client = _cookie_client()
    login = client.post("/api/v1/auth/login/", CREDENTIALS, format="json")
    client.credentials(
        HTTP_X_AUTH_MODE="cookie",
        HTTP_ORIGIN=ORIGIN,
        HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}",
    )

    response = client.post(
        "/api/v1/auth/password/change/",
        {
            "current_password": CREDENTIALS["password"],
            "new_password": "another-long-passphrase",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["access"]
    assert "refresh" not in response.json()
    # The other browser is signed out...
    replay = APIClient().post(
        "/api/v1/auth/refresh/", {"refresh": other_session}, format="json"
    )
    assert replay.status_code == 401
    # ...while this one keeps its fresh cookie.
    assert client.post("/api/v1/auth/refresh/", {}, format="json").status_code == 200


@pytest.mark.django_db
def test_password_change_refuses_a_wrong_current_password(user: Any) -> None:
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/api/v1/auth/password/change/",
        {"current_password": "nope", "new_password": "another-long-passphrase"},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["code"] == "current_password_invalid"


@pytest.mark.django_db
def test_any_password_change_path_revokes_refresh_tokens(user: Any) -> None:
    """`manage.py changepassword` and the admin end in the same save()."""
    APIClient().post("/api/v1/auth/login/", CREDENTIALS, format="json")
    assert OutstandingToken.objects.filter(user=user).count() == 1

    user.set_password("changed-from-the-shell")
    user.save()

    assert BlacklistedToken.objects.filter(token__user=user).count() == 1

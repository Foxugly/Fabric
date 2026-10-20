"""M3: a target's base_url is user input; the app token goes to PushIT only."""

from __future__ import annotations

import io
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.api_auth.pushit_urls import pushit_url_error
from apps.commands import notifications


@pytest.fixture
def client(db: Any) -> APIClient:
    user = get_user_model().objects.create_user(username="ssrf", password="pw")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.mark.parametrize(
    "url",
    [
        "http://pushit-api.foxugly.com",
        "file:///etc/passwd",
        "https://127.0.0.1",
        "https://169.254.169.254",
        "https://pushit-api.foxugly.com.evil.example",
        "https://evil.example/@pushit-api.foxugly.com",
        "https://user:pw@pushit-api.foxugly.com",
        "https://pushit-api.foxugly.com:8001",
        "https://pushit-api.foxugly.com/redirect?to=x",
        "gopher://pushit-api.foxugly.com",
        "not a url",
    ],
)
def test_unsafe_base_urls_are_rejected(url: str) -> None:
    assert pushit_url_error(url) is not None


@pytest.mark.parametrize(
    "url",
    [
        "https://pushit-api.foxugly.com",
        "https://pushit-api.foxugly.com/",
        "https://PUSHIT-API.foxugly.com:443",
    ],
)
def test_the_pushit_origin_is_accepted(url: str) -> None:
    assert pushit_url_error(url) is None


def test_the_allow_list_is_a_setting(settings: Any) -> None:
    settings.PUSHIT_ALLOWED_HOSTS = ["push.example.org"]
    assert pushit_url_error("https://push.example.org") is None
    assert pushit_url_error("https://pushit-api.foxugly.com") is not None


def test_the_api_refuses_an_internal_base_url(client: APIClient) -> None:
    response = client.post(
        "/api/v1/notification-targets/",
        {"name": "x", "app_token": "apt_1", "base_url": "http://127.0.0.1:8000"},
        format="json",
    )

    assert response.status_code == 400
    assert "base_url" in response.json()


def test_the_api_accepts_the_pushit_origin(client: APIClient) -> None:
    response = client.post(
        "/api/v1/notification-targets/",
        {
            "name": "phone",
            "app_token": "apt_1",
            "base_url": "https://pushit-api.foxugly.com/",
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["base_url"] == "https://pushit-api.foxugly.com"


def _destination(base_url: str) -> notifications.Destination:
    return notifications.Destination(
        app_token="apt_secret",
        base_url=base_url,
        title_prefix="Fabric",
        policy={"permission_request": True},
    )


def test_a_stored_unsafe_url_is_never_contacted() -> None:
    """Rows saved before the allow-list existed are refused at send time."""
    with patch.object(notifications._OPENER, "open") as opened:
        notifications._post(
            _destination("http://169.254.169.254"), b"{}", "k", "permission_request"
        )

    opened.assert_not_called()


def test_the_pushit_origin_is_contacted() -> None:
    with patch.object(notifications._OPENER, "open") as opened:
        notifications._post(
            _destination("https://pushit-api.foxugly.com"),
            b"{}",
            "k",
            "permission_request",
        )

    request = opened.call_args[0][0]
    assert request.full_url == (
        "https://pushit-api.foxugly.com/api/v1/notifications/app/send/"
    )


def test_redirects_are_not_followed() -> None:
    """A 3xx must not carry X-App-Token to another host."""
    handler = notifications._NoRedirect()
    request = urllib.request.Request("https://pushit-api.foxugly.com/x")
    redirected = handler.redirect_request(
        request, io.BytesIO(), 302, "Found", {}, "http://127.0.0.1/"
    )
    assert redirected is None

    opener_handlers = getattr(notifications._OPENER, "handlers")  # noqa: B009
    assert any(isinstance(h, notifications._NoRedirect) for h in opener_handlers)
    assert not any(
        type(h) is urllib.request.HTTPRedirectHandler for h in opener_handlers
    )


def test_a_redirect_answer_is_logged_not_followed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = urllib.error.HTTPError(
        "https://pushit-api.foxugly.com/", 302, "Found", {}, None  # type: ignore[arg-type]
    )
    with (
        patch.object(notifications._OPENER, "open", side_effect=error),
        caplog.at_level("WARNING"),
    ):
        notifications._post(
            _destination("https://pushit-api.foxugly.com"),
            b"{}",
            "k",
            "permission_request",
        )

    assert "HTTP 302" in caplog.text

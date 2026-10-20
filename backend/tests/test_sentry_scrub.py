"""I4: nothing secret and no command output reaches Sentry."""

from __future__ import annotations

import importlib
import json
from typing import Any
from unittest.mock import patch

import pytest

from config.sentry import FILTERED, before_send, init_options


def _event() -> dict[str, Any]:
    return {
        "message": "refresh failed for token=eyJhbGciOi.secret",
        "request": {
            "url": "https://fabric.foxugly.com/ws/v1/events/?ticket=abc123",
            "query_string": "ticket=abc123",
            "cookies": {"fabric_refresh": "eyJrefresh"},
            "data": {"password": "hunter2"},
            "headers": {
                "Authorization": "Bearer eyJaccess.token.value",
                "Cookie": "fabric_refresh=eyJrefresh",
                "X-App-Token": "apt_secret",
                "User-Agent": "pytest",
            },
        },
        "extra": {
            "payload": {"command_id": "c1", "delta": "AWS_SECRET=xyz"},
            "result": {"stdout": "DB_PASSWORD=pw"},
            "agent": "pc-1",
        },
        "breadcrumbs": {
            "values": [
                {"message": "GET /api/v1/x?token=leak", "data": {"input": "env"}}
            ]
        },
        "exception": {
            "values": [
                {
                    "type": "KeyError",
                    "value": "'command_id'",
                    "stacktrace": {
                        "frames": [{"function": "receive", "vars": {"payload": "x"}}]
                    },
                }
            ]
        },
    }


def test_before_send_scrubs_credentials_and_output() -> None:
    event = before_send(_event(), {})
    dumped = json.dumps(event)

    for secret in (
        "eyJhbGciOi",
        "abc123",
        "eyJrefresh",
        "hunter2",
        "eyJaccess",
        "apt_secret",
        "AWS_SECRET",
        "DB_PASSWORD",
        "leak",
    ):
        assert secret not in dumped, secret

    headers = event["request"]["headers"]
    assert headers["Authorization"] == FILTERED
    assert headers["X-App-Token"] == FILTERED
    assert headers["User-Agent"] == "pytest"
    assert event["request"]["query_string"] == FILTERED
    assert "data" not in event["request"]
    assert "cookies" not in event["request"]
    assert event["extra"]["agent"] == "pc-1"
    frame = event["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert "vars" not in frame


def test_sentry_is_initialised_without_pii_or_local_variables() -> None:
    options = init_options(
        dsn="https://k@o1.ingest.de.sentry.io/2",
        environment="PROD",
        release=None,
        traces_sample_rate=0.0,
    )

    assert options["send_default_pii"] is False
    assert options["include_local_variables"] is False
    assert options["max_request_body_size"] == "never"
    assert options["before_send"] is before_send


def test_settings_hand_the_scrubber_to_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in {
        "STATE": "PROD",
        "SENTRY_DSN": "https://k@o1.ingest.de.sentry.io/2",
        "SECRET_KEY": "x" * 40,
        "REDIS_URL": "redis://127.0.0.1:6379/9",
    }.items():
        monkeypatch.setenv(key, value)
    try:
        with patch("sentry_sdk.init") as init:
            importlib.reload(importlib.import_module("config.settings"))
        kwargs = init.call_args.kwargs
        assert kwargs["include_local_variables"] is False
        assert kwargs["send_default_pii"] is False
        assert kwargs["before_send"] is before_send
    finally:
        monkeypatch.undo()
        importlib.reload(importlib.import_module("config.settings"))

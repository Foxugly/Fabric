"""sentry-sdk 3.0: init, scrubbing and logging through a real client.

The client sends to an in-memory transport, so what is asserted is what would
actually leave the box.
"""

from __future__ import annotations

import json
import logging
import warnings
from collections.abc import Iterator
from typing import Any, cast

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from config.sentry import (
    DATA_COLLECTION,
    FILTERED,
    before_send_span,
    init_options,
)
from config.sentry_scrub import scrub_event, scrub_span, scrub_text

SECRET = "s3cr3t-value-never-sent"


class _Capture(Transport):
    def __init__(self) -> None:
        super().__init__(None)
        self.envelopes: list[Envelope] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)

    def payloads(self, kind: str | None = None) -> list[bytes]:
        return [
            item.payload.get_bytes()
            for envelope in self.envelopes
            for item in envelope.items
            if kind is None or item.headers.get("type") == kind
        ]


@pytest.fixture
def sentry(request: pytest.FixtureRequest) -> Iterator[_Capture]:
    """`sentry_sdk.init` with the production options, into memory."""
    transport = _Capture()
    options = init_options(
        dsn="https://public@sentry.example.invalid/1",
        environment="test",
        release=None,
        traces_sample_rate=getattr(request, "param", 0.0),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no deprecated option, no unknown key
        sentry_sdk.init(transport=transport, **options)
    yield transport
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)


def _events(transport: _Capture) -> list[dict[str, Any]]:
    sentry_sdk.flush()
    return [json.loads(raw) for raw in transport.payloads("event")]


def test_captured_exception_is_scrubbed_end_to_end(sentry: _Capture) -> None:
    def view() -> None:
        password = SECRET  # noqa: F841 (a local Sentry would ship if allowed)
        raise ValueError("boom")

    with sentry_sdk.new_scope() as scope:
        scope.set_extra("token", SECRET)
        scope.set_extra("api_key", SECRET)
        scope.set_extra("page", 3)
        try:
            view()
        except ValueError:
            sentry_sdk.capture_exception()

    (event,) = _events(sentry)
    assert event["extra"]["token"] == FILTERED
    assert event["extra"]["api_key"] == FILTERED
    assert event["extra"]["page"] == 3
    frame = event["exception"]["values"][-1]["stacktrace"]["frames"][-1]
    assert frame["function"] == "view"
    assert "vars" not in frame  # stack_frame_variables stays off
    assert SECRET not in json.dumps(event)


def test_scrub_event_covers_the_removed_sdk_scrubber() -> None:
    event = scrub_event(
        {
            "request": {
                "data": {"password": SECRET, "n": 1},
                "headers": {"X-Real-Ip": SECRET, "Accept": "*/*"},
                "cookies": {"sessionid": SECRET},
            },
            "extra": {"private_key": SECRET},
            "user": {"id": "7", "ip_address": SECRET},
            "breadcrumbs": {"values": [{"data": {"apikey": SECRET, "n": 1}}]},
            "exception": {
                "values": [{"stacktrace": {"frames": [{"vars": {"passwd": SECRET}}]}}]
            },
        },
        {},
    )
    assert SECRET not in json.dumps(event)
    assert event["request"]["headers"]["Accept"] == "*/*"
    assert "ip_address" not in event["user"]


def test_scrubbers_survive_odd_shapes() -> None:
    """In 3.0 an exception in an event callback drops the event."""
    for event in ({}, {"request": "x"}, {"exception": {"values": [None]}}):
        assert scrub_event(event, {}) is event
    assert scrub_span({}, {}) == {}
    assert before_send_span({"name": None, "attributes": None}, {})["name"] is None


def test_before_send_span_masks_name_and_attributes() -> None:
    span = before_send_span(
        {
            "name": "GET /ws/v1/events/?ticket=" + SECRET,
            "attributes": {
                "http.request.header.authorization": [SECRET],
                "http.request.header.x-app-token": [SECRET],
                "url.full": "https://x.invalid/?token=" + SECRET + "&page=2",
                "http.request.method": "GET",
            },
        },
        {},
    )
    assert SECRET not in json.dumps(span)
    assert span["name"] == "GET /ws/v1/events/?ticket=" + FILTERED
    assert span["attributes"]["http.request.method"] == "GET"


@pytest.mark.parametrize("sentry", [1.0], indirect=True)
def test_sent_spans_are_scrubbed(sentry: _Capture) -> None:
    with sentry_sdk.start_span(
        name="GET /reset?token=" + SECRET,
        attributes={"session_key": SECRET, "page": 2},
    ):
        pass
    sentry_sdk.flush()
    sent = b"".join(sentry.payloads()).decode("utf-8", "replace")
    assert "/reset?token=" + FILTERED in sent
    assert SECRET not in sent


def test_logger_error_still_creates_an_event(sentry: _Capture) -> None:
    logging.getLogger("apps.commands").error("command %s failed", "c1")
    events = _events(sentry)
    assert [e["logentry"]["message"] for e in events] == ["command %s failed"]
    assert "stacktrace" not in json.dumps(events)  # attach_stacktrace stays off


# --- Security review follow-up: contexts, embedded spans, URLs, response headers.

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOjF9.c2lnbmF0dXJlMTIz"


def test_scrub_event_scrubs_contexts_recursively() -> None:
    event = scrub_event(
        {
            "contexts": {
                "response": {"headers": {"Set-Cookie": SECRET, "X-Ok": "y"}},
                "trace": {"data": {"url.full": "https://h.invalid/?token=" + SECRET}},
            }
        },
        {},
    )
    assert SECRET not in json.dumps(event)
    assert event["contexts"]["response"]["headers"]["X-Ok"] == "y"


def test_scrub_event_scrubs_embedded_spans() -> None:
    event = scrub_event(
        {
            "spans": [
                {
                    "description": "GET /reset-password/" + SECRET + "/",
                    "data": {"authorization": SECRET, "http.query": "token=" + SECRET},
                }
            ]
        },
        {},
    )
    assert SECRET not in json.dumps(event)
    assert event["spans"][0]["description"] == "GET /reset-password/[Filtered]/"


def test_scrub_text_drops_url_userinfo() -> None:
    text = scrub_text("fetch https://bob:" + SECRET + "@h.invalid/a?page=2")
    assert text == "fetch https://h.invalid/a?page=2"


def test_scrub_text_masks_secret_path_segments() -> None:
    assert (
        scrub_text("GET /auth/magic-link/abc123/") == "GET /auth/magic-link/[Filtered]/"
    )
    assert (
        scrub_text("GET /invitation/xyz/accept") == "GET /invitation/[Filtered]/accept"
    )
    assert scrub_text("GET /files/" + JWT + "/") == "GET /files/[Filtered]/"
    # Route templates and ordinary paths stay readable.
    assert scrub_text("GET /api/reset/<token>/") == "GET /api/reset/<token>/"
    assert scrub_text("GET /api/v1/invoices/42/") == "GET /api/v1/invoices/42/"


def test_scrub_span_masks_url_paths_in_attributes() -> None:
    span = scrub_span(
        {
            "name": "GET",
            "attributes": {"url.full": "https://u:p@h.invalid/activate/" + SECRET},
        },
        {},
    )
    assert span["attributes"]["url.full"] == "https://h.invalid/activate/[Filtered]"


def test_response_headers_are_never_collected() -> None:
    assert DATA_COLLECTION["http_headers"]["response"] == {"mode": "off"}
    client = sentry_sdk.Client(
        dsn=None, default_integrations=False, data_collection=cast(Any, DATA_COLLECTION)
    )
    # 3.0.0 has no response-header setting (it collects none); if a release adds
    # one, it must resolve to "off".
    headers = client.options["data_collection"]["http_headers"]
    assert headers.get("response", {"mode": "off"})["mode"] == "off"


# --- Second security review: exception values, query/fragment, userinfo, fail closed.


def test_exception_and_thread_values_are_scrubbed() -> None:
    event = scrub_event(
        {
            "exception": {
                "values": [{"value": "GET https://h.invalid/x?token=" + SECRET}]
            },
            "threads": {"values": [{"value": "retry with api_key=" + SECRET}]},
        },
        {},
    )
    assert SECRET not in json.dumps(event)


def test_query_and_fragment_values_are_masked() -> None:
    url = "https://h.invalid/cb?page=2&ticket=t&sig=AbCdEfGhIjKlMnOp#access_token=x"
    assert scrub_text(url) == (
        "https://h.invalid/cb?page=2&ticket=[Filtered]&sig=[Filtered]#[Filtered]"
    )
    assert scrub_text("next=/a&code=0123456789abcdef") == "next=/a&code=[Filtered]"
    event = scrub_event({"request": {"query_string": "page=2&k=" + JWT}}, {})
    assert event["request"]["query_string"] == "page=2&k=[Filtered]"


def test_userinfo_is_stripped_robustly() -> None:
    assert scrub_text("https://u:pa@ss@h.invalid/") == "https://h.invalid/"
    assert scrub_text("https://u:p/w@h.invalid/x") == "https://h.invalid/x"
    assert scrub_text("https://user:" + SECRET + "/x") == "https://[Filtered]/x"
    assert scrub_text("https://h.invalid:8443/a") == "https://h.invalid:8443/a"
    assert scrub_text("http://[::1]:8000/a") == "http://[::1]:8000/a"


def test_scrubbing_fails_closed() -> None:
    deep: Any = SECRET
    for _ in range(30):
        deep = [deep]
    event = scrub_event(
        {
            "extra": {
                "t": ("a?token=" + SECRET,),
                "s": {"x?password=" + SECRET},
                "d": deep,
            }
        },
        {},
    )
    assert SECRET not in json.dumps(event)

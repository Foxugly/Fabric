"""Sentry options and the `before_send` scrubber (I4, OPERATIONS.md §3.25).

What must never reach Sentry from Fabric:

- credentials: the `Authorization` header (JWT, agent token), cookies (the
  refresh cookie), `X-App-Token`, and `token=` / `ticket=` in a query string;
- command output: an exception raised while handling an agent frame would
  otherwise embed the frame (`payload`, `result`, `delta`, `input`...), and a
  `Get-Content .env` is exactly that.

`DATA_COLLECTION` (sentry-sdk 3.0 removed `send_default_pii`) collects no user,
cookies, request bodies or local variables. 3.0 also removed the SDK's own
EventScrubber: `config.sentry_scrub` replaces it as the first step of
`before_send`; this scrubber is then the backstop for whatever else ends up in
headers, `extra`, contexts or breadcrumbs. Spans go through `before_send_span`
(3.0 removed `before_send_transaction`): same denylists on name and attributes.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from config.sentry_scrub import FILTERED as FILTERED  # re-exported
from config.sentry_scrub import scrub_event, scrub_span

#: Keys whose value is dropped wherever they appear (compared lower-cased,
#: `-` and `_` treated alike).
SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "proxy_authorization",
        "cookie",
        "cookies",
        "set_cookie",
        "x_app_token",
        "app_token",
        "token",
        "access",
        "refresh",
        "ticket",
        "password",
        "current_password",
        "new_password",
        "secret",
        "api_key",
        "sentry_dsn",
        "dsn",
        # Command content: what the agent ran and what it printed.
        "payload",
        "parameters",
        "result",
        "output",
        "stdout",
        "stderr",
        "delta",
        "text",
        "content",
        "input",
        "tool_input",
        "updated_input",
        "error",
        "data",
        "body",
        "query_string",
        "fingerprint",
    }
)

#: `token=…`, `ticket=…` and friends inside any string (URLs, messages).
_QUERY_SECRET = re.compile(
    r"(?i)\b(token|ticket|access|refresh|password|code|key)=([^&\s\"']+)"
)
_BEARER = re.compile(r"(?i)\b(bearer|token)\s+[A-Za-z0-9._~+/=-]{8,}")


# Proxy/IP header terms the 3.0 migration guide gives to match send_default_pii=False.
_PII_TERMS = ["forwarded", "-ip", "remote-", "via", "-user"]

#: What the SDK may collect: never more than with `send_default_pii=False`.
DATA_COLLECTION: dict[str, Any] = {
    "user_info": False,
    "cookies": {"mode": "off"},
    # Response headers (Set-Cookie...): sentry-sdk 3.0.0 has no such setting and
    # collects none; declared off so a later 3.x that adds it keeps them out.
    # `before_send` also scrubs `contexts` (response headers included).
    "http_headers": {
        "request": {"mode": "denylist", "terms": _PII_TERMS},
        "response": {"mode": "off"},
    },
    "http_bodies": [],
    "url_query_params": {"mode": "denylist", "terms": _PII_TERMS},
    "graphql": {"document": False, "variables": False},
    "gen_ai": {"inputs": False, "outputs": False},
    "mcp": {"inputs": False, "outputs": False},
    "database_query_data": False,
    "queues": False,
    "stack_frame_variables": False,
    "frame_context_lines": 5,
}


def _normalise(key: str) -> str:
    return key.lower().replace("-", "_")


def scrub_string(value: str) -> str:
    value = _QUERY_SECRET.sub(lambda m: f"{m.group(1)}={FILTERED}", value)
    return _BEARER.sub(lambda m: f"{m.group(1)} {FILTERED}", value)


def scrub(value: Any) -> Any:
    """Recursively drop sensitive keys and mask secrets inside strings."""
    if isinstance(value, dict):
        return {
            key: (
                FILTERED
                if isinstance(key, str) and _normalise(key) in SENSITIVE_KEYS
                else scrub(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        # Headers sometimes come as [name, value] pairs.
        if (
            len(value) == 2
            and isinstance(value[0], str)
            and _normalise(value[0]) in SENSITIVE_KEYS
        ):
            return [value[0], FILTERED]
        return [scrub(item) for item in value]
    if isinstance(value, str):
        return scrub_string(value)
    return value


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    scrub_event(event, hint)
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        if "query_string" in request:
            request["query_string"] = FILTERED
        if isinstance(request.get("url"), str):
            request["url"] = scrub_string(request["url"])
        if "headers" in request:
            request["headers"] = scrub(request["headers"])
        if "env" in request:
            request["env"] = scrub(request["env"])

    for section in ("extra", "contexts", "tags", "user"):
        if section in event:
            event[section] = scrub(event[section])

    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict) and isinstance(breadcrumbs.get("values"), list):
        breadcrumbs["values"] = [scrub(crumb) for crumb in breadcrumbs["values"]]

    # Defence in depth: local variables are off, but strip them if present.
    for exception in (event.get("exception") or {}).get("values") or []:
        if isinstance(exception.get("value"), str):
            exception["value"] = scrub_string(exception["value"])
        for frame in (exception.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)

    if isinstance(event.get("logentry"), dict):
        event["logentry"] = scrub(event["logentry"])
    if isinstance(event.get("message"), str):
        event["message"] = scrub_string(event["message"])
    return event


def before_send_span(span: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """`before_send_span`: a span can be rewritten here, never dropped (3.0)."""
    scrub_span(span, hint)
    if isinstance(span.get("name"), str):
        span["name"] = scrub_string(span["name"])
    attributes = span.get("attributes")
    if isinstance(attributes, dict):
        for key, value in list(attributes.items()):
            if _normalise(str(key).rsplit(".", 1)[-1]) in SENSITIVE_KEYS:
                attributes[key] = FILTERED
            elif isinstance(value, str):
                attributes[key] = scrub_string(value)
            elif isinstance(value, list):
                attributes[key] = [
                    scrub_string(item) if isinstance(item, str) else item
                    for item in value
                ]
    return span


def init_options(
    *, dsn: str, environment: str, release: str | None, traces_sample_rate: float
) -> dict[str, Any]:
    """Keyword arguments for `sentry_sdk.init` (kept here to be testable)."""
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    return {
        "dsn": dsn,
        "environment": environment,
        "release": release,
        "traces_sample_rate": traces_sample_rate,
        "data_collection": DATA_COLLECTION,
        "max_request_body_size": "never",
        # 3.0 turned this on: a plain message would carry the stack.
        "attach_stacktrace": False,
        "integrations": [
            # 3.0 traces cache calls by default: keep 2.x's span volume.
            DjangoIntegration(cache_spans=False),
            # 3.0 no longer enables logging by default and its `event_level` is
            # None: without this, `logger.error()` would stop creating issues.
            # `level=None`: no Sentry Logs, which 2.x did not send either.
            LoggingIntegration(
                level=None,
                event_level=logging.ERROR,
                breadcrumb_level=logging.INFO,
            ),
        ],
        "before_send": before_send,
        "before_send_span": before_send_span,
    }

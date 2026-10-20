"""Sentry options and the `before_send` scrubber (I4, OPERATIONS.md §3.25).

What must never reach Sentry from Fabric:

- credentials: the `Authorization` header (JWT, agent token), cookies (the
  refresh cookie), `X-App-Token`, and `token=` / `ticket=` in a query string;
- command output: an exception raised while handling an agent frame would
  otherwise embed the frame (`payload`, `result`, `delta`, `input`...), and a
  `Get-Content .env` is exactly that.

Local variables are not captured at all (`include_local_variables=False`), the
request body is never attached, and this scrubber is the backstop for whatever
else ends up in headers, `extra`, contexts or breadcrumbs.
"""

from __future__ import annotations

import re
from typing import Any

FILTERED = "[Filtered]"

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


def init_options(
    *, dsn: str, environment: str, release: str | None, traces_sample_rate: float
) -> dict[str, Any]:
    """Keyword arguments for `sentry_sdk.init` (kept here to be testable)."""
    return {
        "dsn": dsn,
        "environment": environment,
        "release": release,
        "traces_sample_rate": traces_sample_rate,
        "send_default_pii": False,
        "include_local_variables": False,
        "max_request_body_size": "never",
        "before_send": before_send,
    }

"""Which PushIT endpoints Fabric may POST an app token to (SSRF guard, M3).

`PushItTarget.base_url` is user input, and the server POSTs the target's
`X-App-Token` to it. Left free, it reaches anything the box can: `file://`
(urllib), `http://127.0.0.1:...` (every backend of the fleet), the EC2 metadata
service. Only `https` to a host of `PUSHIT_ALLOWED_HOSTS` is accepted, and the
request never follows a redirect (see `notifications._OPENER`).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings


def allowed_hosts() -> set[str]:
    hosts = getattr(settings, "PUSHIT_ALLOWED_HOSTS", ["pushit-api.foxugly.com"])
    return {host.strip().lower().rstrip(".") for host in hosts if host.strip()}


def pushit_url_error(url: str) -> str | None:
    """Why `url` may not be used as a PushIT base URL, or None when it may."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return "base_url is not a valid URL"
    if parts.scheme != "https":
        return "base_url must use https"
    if parts.username is not None or parts.password is not None:
        return "base_url must not carry credentials"
    host = (parts.hostname or "").lower().rstrip(".")
    if host not in allowed_hosts():
        return "base_url host is not an allowed PushIT host"
    if port not in (None, 443):
        return "base_url must use the default https port"
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return "base_url must be the bare origin (no path, query or fragment)"
    return None


def is_allowed_pushit_url(url: str) -> bool:
    return pushit_url_error(url) is None

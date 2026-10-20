from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from fabric_agent.token_store import load_agent_token

#: Development escape hatch for `ws://`, honoured for loopback hosts only.
ALLOW_INSECURE_ENV = "FABRIC_AGENT_ALLOW_INSECURE"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class InsecureTransportError(RuntimeError):
    """The server URL would send the agent token in clear text."""


@dataclass(slots=True, frozen=True)
class AgentConfig:
    server_ws_url: str
    agent_id: str
    agent_token: str
    heartbeat_seconds: int = 10
    connectivity_check_seconds: int = 5
    connectivity_failure_threshold: int = 3
    network_recovery_enabled: bool = False
    network_recovery_cooldown_seconds: int = 300
    network_recovery_adapter_name: str | None = None
    allow_insecure_transport: bool = False

    @classmethod
    def from_env(cls) -> AgentConfig:
        server_ws_url = _require_env("FABRIC_SERVER_WS_URL")
        allow_insecure = _is_truthy(os.environ.get(ALLOW_INSECURE_ENV, ""))
        ensure_secure_server_url(server_ws_url, allow_insecure=allow_insecure)
        return cls(
            server_ws_url=server_ws_url,
            agent_id=_require_env("FABRIC_AGENT_ID"),
            agent_token=load_agent_token(),
            heartbeat_seconds=int(
                os.environ.get("FABRIC_AGENT_HEARTBEAT_SECONDS", "10")
            ),
            connectivity_check_seconds=int(
                os.environ.get("FABRIC_CONNECTIVITY_CHECK_SECONDS", "5")
            ),
            connectivity_failure_threshold=int(
                os.environ.get("FABRIC_CONNECTIVITY_FAILURE_THRESHOLD", "3")
            ),
            network_recovery_enabled=(
                os.environ.get("FABRIC_NETWORK_RECOVERY_ENABLED", "false").lower()
                == "true"
            ),
            network_recovery_cooldown_seconds=int(
                os.environ.get("FABRIC_NETWORK_RECOVERY_COOLDOWN_SECONDS", "300")
            ),
            network_recovery_adapter_name=_optional_env(
                "FABRIC_NETWORK_RECOVERY_ADAPTER_NAME"
            ),
            allow_insecure_transport=allow_insecure,
        )


def ensure_secure_server_url(url: str, *, allow_insecure: bool) -> None:
    """Only `wss://`, or `ws://` to loopback behind an explicit dev flag."""
    parsed = urlparse(url)
    if parsed.scheme == "wss":
        return
    if parsed.scheme != "ws":
        raise InsecureTransportError(
            f"FABRIC_SERVER_WS_URL must use wss:// (got {parsed.scheme or 'none'}://)"
        )
    if not allow_insecure:
        raise InsecureTransportError(
            "Refusing ws:// : the agent token and the commands would travel in "
            f"clear text. Use wss://, or set {ALLOW_INSECURE_ENV}=1 for a local "
            "development server on localhost/127.0.0.1"
        )
    if (parsed.hostname or "").lower() not in LOOPBACK_HOSTS:
        raise InsecureTransportError(
            f"{ALLOW_INSECURE_ENV} only allows ws:// to localhost/127.0.0.1, "
            f"not {parsed.hostname!r}"
        )


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def _optional_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}

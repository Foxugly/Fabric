from __future__ import annotations

import os
from dataclasses import dataclass


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

    @classmethod
    def from_env(cls) -> AgentConfig:
        return cls(
            server_ws_url=_require_env("FABRIC_SERVER_WS_URL"),
            agent_id=_require_env("FABRIC_AGENT_ID"),
            agent_token=_require_env("FABRIC_AGENT_TOKEN"),
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
        )


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def _optional_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None

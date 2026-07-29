from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ManualActionType = Literal["local_session_setup", "browser_login", "reauthorize"]
TransportType = Literal["local_session", "browser_surface", "unknown"]


@dataclass(slots=True, frozen=True)
class ActionRequired:
    type: ManualActionType
    provider: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class SessionStatus:
    session_detected: bool
    session_ready: bool
    manual_action_required: bool
    transport: TransportType
    provider: str = "claude_code_local"
    action_required: ActionRequired | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.action_required is None:
            data["action_required"] = None
        if not self.details:
            data.pop("details", None)
        return data


@dataclass(slots=True, frozen=True)
class ClaudeCodeLocalCapabilities:
    provider: str = "claude_code_local"
    capabilities: tuple[str, ...] = (
        "session.status",
        "session.attach",
        "message.send",
        "message.stream",
        "message.cancel",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "capabilities": list(self.capabilities),
        }

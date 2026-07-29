from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from shared.protocol import PROVIDER_ACTIONS, qualify

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
    """The actions this provider really implements, from the shared catalogue.

    `session.attach` and `message.cancel` used to be advertised here without any
    matching action: attaching is implicit through `--resume`, and cancellation
    goes through the generic `command.cancel` protocol message.
    """

    provider: str = "claude_code_local"

    @property
    def capabilities(self) -> tuple[str, ...]:
        return PROVIDER_ACTIONS[self.provider]

    def qualified_capabilities(self) -> tuple[str, ...]:
        return tuple(qualify(self.provider, action) for action in self.capabilities)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "capabilities": list(self.qualified_capabilities()),
        }

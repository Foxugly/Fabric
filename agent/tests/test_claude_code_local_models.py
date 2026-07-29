from __future__ import annotations

from fabric_agent.providers.claude_code_local.detector import (
    ClaudeCodeSessionStatusDetector,
)
from fabric_agent.providers.claude_code_local.models import (
    ActionRequired,
    ClaudeCodeLocalCapabilities,
    SessionStatus,
)
from fabric_agent.providers.claude_code_local.provider import ClaudeCodeLocalProvider


class StubStatusDetector(ClaudeCodeSessionStatusDetector):
    def __init__(self, status: SessionStatus) -> None:
        self._status = status

    def detect(self) -> SessionStatus:
        return self._status


def test_claude_code_local_capabilities_serialize() -> None:
    capabilities = ClaudeCodeLocalCapabilities()

    assert capabilities.to_dict() == {
        "provider": "claude_code_local",
        "capabilities": [
            "claude_code_local.session.status",
            "claude_code_local.message.send",
        ],
    }


def test_session_status_with_action_required_serializes() -> None:
    action_required = ActionRequired(
        type="local_session_setup",
        provider="claude_code_local",
        message="Open a local Claude Code session.",
    )

    status = SessionStatus(
        session_detected=False,
        session_ready=False,
        manual_action_required=True,
        transport="unknown",
        action_required=action_required,
    )

    assert status.to_dict() == {
        "provider": "claude_code_local",
        "session_detected": False,
        "session_ready": False,
        "manual_action_required": True,
        "transport": "unknown",
        "action_required": {
            "type": "local_session_setup",
            "provider": "claude_code_local",
            "message": "Open a local Claude Code session.",
        },
    }


async def test_claude_code_local_provider_status_requires_manual_action() -> None:
    provider = ClaudeCodeLocalProvider(
        status_detector=StubStatusDetector(
            SessionStatus(
                session_detected=False,
                session_ready=False,
                manual_action_required=True,
                transport="unknown",
                action_required=ActionRequired(
                    type="local_session_setup",
                    provider="claude_code_local",
                    message="Open a local Claude Code session.",
                ),
            )
        )
    )

    status = await provider.get_status()

    assert status["provider"] == "claude_code_local"
    assert status["manual_action_required"] is True
    assert status["action_required"]["type"] == "local_session_setup"

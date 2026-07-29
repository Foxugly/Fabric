"""Single source of truth for the Fabric action catalogue.

Both sides of the WebSocket read this module, so an action can never exist on
one side only. Actions are **qualified** on the wire (`<provider>.<action>`);
providers implement the unqualified name and the dispatcher strips the prefix.
"""

from __future__ import annotations

PROVIDER_ACTIONS: dict[str, tuple[str, ...]] = {
    "echo": ("message.send",),
    "claude_code_local": (
        "session.status",
        "message.send",
    ),
    "windows_powershell": (
        "system.info",
        "process.list",
        "claude.version",
        "session.create",
        "session.status",
        "session.close",
        "command.run",
        "network.recover",
    ),
}


def qualify(provider: str, action: str) -> str:
    return f"{provider}.{action}"


def qualified_actions(provider: str) -> tuple[str, ...]:
    return tuple(qualify(provider, action) for action in PROVIDER_ACTIONS[provider])


def all_qualified_actions() -> set[str]:
    return {
        qualify(provider, action)
        for provider, actions in PROVIDER_ACTIONS.items()
        for action in actions
    }


def qualified_actions_by_provider() -> dict[str, set[str]]:
    return {provider: set(qualified_actions(provider)) for provider in PROVIDER_ACTIONS}


def local_action(provider: str, action: str) -> str:
    """Strip the provider namespace: `echo.message.send` -> `message.send`."""
    prefix = f"{provider}."
    if action.startswith(prefix):
        return action[len(prefix) :]
    return action

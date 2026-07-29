"""The contract that P0-1 broke: every catalogued action must be dispatchable.

The backend derives its `ALLOWED_ACTIONS` from the same shared catalogue, so a
provider that renames or drops an action fails here instead of failing at
runtime with `NotImplementedError` on a real command.
"""

from __future__ import annotations

import pytest
from shared.protocol import PROVIDER_ACTIONS, local_action, qualify

from fabric_agent.application.registry import ProviderRegistry


def test_registry_covers_every_catalogued_provider() -> None:
    registry = ProviderRegistry()

    assert set(registry.names()) == set(PROVIDER_ACTIONS)


@pytest.mark.parametrize(
    ("provider_name", "action"),
    [
        (provider_name, action)
        for provider_name, actions in PROVIDER_ACTIONS.items()
        for action in actions
    ],
)
def test_provider_declares_every_catalogued_action(
    provider_name: str,
    action: str,
) -> None:
    provider = ProviderRegistry().get(provider_name)

    assert action in provider.actions


@pytest.mark.asyncio
async def test_capabilities_are_advertised_qualified() -> None:
    registry = ProviderRegistry()

    advertised: set[str] = set()
    for _, provider in registry.items():
        advertised.update(await provider.get_capabilities())

    assert advertised == {
        qualify(provider_name, action)
        for provider_name, actions in PROVIDER_ACTIONS.items()
        for action in actions
    }


def test_local_action_strips_only_the_matching_prefix() -> None:
    assert local_action("echo", "echo.message.send") == "message.send"
    assert local_action("echo", "message.send") == "message.send"
    assert local_action("echo", "claude_code_local.message.send") == (
        "claude_code_local.message.send"
    )

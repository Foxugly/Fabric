from __future__ import annotations

from fabric_agent.application.config import AgentConfig
from fabric_agent.application.dispatcher import CommandDispatcher
from fabric_agent.application.registry import ProviderRegistry
from fabric_agent.transport.client import AgentWebSocketClient


async def run_agent() -> None:
    config = AgentConfig.from_env()
    registry = ProviderRegistry()
    for _, provider in registry.items():
        await provider.initialize()
    try:
        dispatcher = CommandDispatcher(registry)
        client = AgentWebSocketClient(config, registry, dispatcher)
        await client.run_forever()
    finally:
        for _, provider in registry.items():
            await provider.shutdown()

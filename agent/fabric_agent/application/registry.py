from __future__ import annotations

from fabric_agent.domain.provider import Provider
from fabric_agent.permissions.gateway import PermissionGateway
from fabric_agent.providers.claude_code_local.provider import ClaudeCodeLocalProvider
from fabric_agent.providers.echo.provider import EchoProvider
from fabric_agent.providers.windows_powershell.provider import WindowsPowerShellProvider


class ProviderRegistry:
    def __init__(self, permission_gateway: PermissionGateway | None = None) -> None:
        self._providers: dict[str, Provider] = {
            "claude_code_local": ClaudeCodeLocalProvider(
                permission_gateway=permission_gateway
            ),
            "echo": EchoProvider(),
            "windows_powershell": WindowsPowerShellProvider(),
        }

    def get(self, name: str) -> Provider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ValueError(f"Unknown provider: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._providers)

    def items(self) -> list[tuple[str, Provider]]:
        return list(self._providers.items())

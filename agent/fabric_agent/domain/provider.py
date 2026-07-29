from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from shared.protocol import qualify


class Provider(ABC):
    name: str

    #: Unqualified actions this provider handles. Must cover the shared
    #: catalogue entry for `name` — see `shared/protocol/actions.py`.
    actions: tuple[str, ...] = ()

    @abstractmethod
    async def initialize(self) -> None:
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        ...

    @abstractmethod
    async def get_status(self) -> dict[str, Any]:
        ...

    async def get_capabilities(self) -> list[str]:
        """Capabilities are advertised qualified, so a flat list stays unambiguous."""
        return [qualify(self.name, action) for action in self.actions]

    @abstractmethod
    async def execute(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    async def cancel(self, action: str, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    def stream(
        self,
        action: str,
        payload: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError

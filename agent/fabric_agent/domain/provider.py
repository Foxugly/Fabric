from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any


class Provider(ABC):
    name: str

    @abstractmethod
    async def initialize(self) -> None:
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        ...

    @abstractmethod
    async def get_status(self) -> dict[str, Any]:
        ...

    @abstractmethod
    async def get_capabilities(self) -> list[str]:
        ...

    @abstractmethod
    async def execute(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    async def cancel(self, action: str, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    def stream(
        self,
        action: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError

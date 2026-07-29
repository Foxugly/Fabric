from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from fabric_agent.domain.provider import Provider


class EchoProvider(Provider):
    name = "echo"
    actions = ("message.send",)

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def get_status(self) -> dict[str, Any]:
        return {"ready": True}

    async def execute(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text", ""))
        return {"text": f"Echo: {text}"}

    async def stream(
        self,
        action: str,
        payload: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        text = str(payload.get("text", ""))
        rendered = f"Echo: {text}"
        buffer = ""
        for index, token in enumerate(rendered.split(" "), start=1):
            delta = token if index == 1 else f" {token}"
            buffer += delta
            await asyncio.sleep(0.05)
            yield {
                "event": "message.delta",
                "sequence": index,
                "delta": delta,
                "snapshot": buffer,
            }

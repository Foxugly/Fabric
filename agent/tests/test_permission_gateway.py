from __future__ import annotations

import asyncio
import json

import pytest

from fabric_agent.permissions import (
    PermissionDecision,
    PermissionGateway,
    PermissionRequest,
)


async def _ask(gateway: PermissionGateway, **overrides: object) -> dict[str, object]:
    """Play the broker's part: connect, ask, read the verdict."""
    reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
    request: dict[str, object] = {
        "token": gateway.token,
        "request_id": "req-1",
        "command_id": "cmd-1",
        "tool_name": "Bash",
        "input": {"command": "git status"},
    }
    request.update(overrides)
    writer.write((json.dumps(request) + "\n").encode("utf-8"))
    await writer.drain()
    raw_response = await asyncio.wait_for(reader.readline(), timeout=5)
    writer.close()
    return dict(json.loads(raw_response.decode("utf-8")))


@pytest.mark.asyncio
async def test_gateway_relays_an_allow_decision() -> None:
    published: list[PermissionRequest] = []
    gateway = PermissionGateway(handler=_recorder(published))
    await gateway.start()
    try:
        asking = asyncio.create_task(_ask(gateway))
        await asyncio.sleep(0.05)

        assert len(published) == 1
        assert published[0].tool_name == "Bash"
        assert gateway.resolve("req-1", PermissionDecision(allowed=True)) is True

        response = await asyncio.wait_for(asking, timeout=5)
    finally:
        await gateway.stop()

    assert response == {
        "behavior": "allow",
        "updatedInput": {"command": "git status"},
    }


@pytest.mark.asyncio
async def test_gateway_relays_a_deny_decision_with_a_reason() -> None:
    gateway = PermissionGateway(handler=_recorder([]))
    await gateway.start()
    try:
        asking = asyncio.create_task(_ask(gateway))
        await asyncio.sleep(0.05)
        gateway.resolve("req-1", PermissionDecision(allowed=False, message="not now"))
        response = await asyncio.wait_for(asking, timeout=5)
    finally:
        await gateway.stop()

    assert response == {"behavior": "deny", "message": "not now"}


@pytest.mark.asyncio
async def test_gateway_rejects_a_bad_token() -> None:
    published: list[PermissionRequest] = []
    gateway = PermissionGateway(handler=_recorder(published))
    await gateway.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
        writer.write(
            (json.dumps({"token": "wrong", "request_id": "x", "tool_name": "Bash"}))
            .encode("utf-8")
            + b"\n"
        )
        await writer.drain()
        # The gateway hangs up without answering rather than leaking anything.
        assert await asyncio.wait_for(reader.readline(), timeout=5) == b""
        writer.close()
    finally:
        await gateway.stop()

    assert published == []


@pytest.mark.asyncio
async def test_losing_fabric_denies_everything_still_waiting() -> None:
    gateway = PermissionGateway(handler=_recorder([]))
    await gateway.start()
    try:
        asking = asyncio.create_task(_ask(gateway))
        await asyncio.sleep(0.05)
        gateway.fail_pending("connection lost")
        response = await asyncio.wait_for(asking, timeout=5)
    finally:
        await gateway.stop()

    assert response["behavior"] == "deny"
    assert response["message"] == "connection lost"


@pytest.mark.asyncio
async def test_a_request_that_cannot_be_published_is_denied() -> None:
    async def failing_handler(request: PermissionRequest) -> None:
        raise ConnectionError("Not connected to Fabric")

    gateway = PermissionGateway(handler=failing_handler)
    await gateway.start()
    try:
        response = await asyncio.wait_for(_ask(gateway), timeout=5)
    finally:
        await gateway.stop()

    assert response["behavior"] == "deny"
    assert "Not connected to Fabric" in str(response["message"])


@pytest.mark.asyncio
async def test_no_handler_means_no_silent_approval() -> None:
    gateway = PermissionGateway()
    await gateway.start()
    try:
        response = await asyncio.wait_for(_ask(gateway), timeout=5)
    finally:
        await gateway.stop()

    assert response["behavior"] == "deny"


def _recorder(published: list[PermissionRequest]):  # type: ignore[no-untyped-def]
    async def handler(request: PermissionRequest) -> None:
        published.append(request)

    return handler

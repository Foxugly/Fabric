from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from asgiref.sync import sync_to_async
from channels.testing.websocket import WebsocketCommunicator
from rest_framework.test import APIClient
from shared.protocol import build_message

from apps.agents.models import Agent
from apps.agents.services import dispatch_command
from apps.commands.models import Command, CommandStatus
from config.asgi import application


async def _receive_event_type(
    communicator: WebsocketCommunicator,
    expected_type: str,
) -> dict[str, Any]:
    for _ in range(8):
        event = cast(dict[str, Any], await communicator.receive_json_from())
        if event["type"] == expected_type:
            return event
    raise AssertionError(f"Did not receive event type {expected_type}")


@pytest.mark.django_db
def test_command_status_transitions() -> None:
    agent = Agent.objects.create(name="agent")
    command = Command.objects.create(
        agent=agent,
        provider="echo",
        action="echo.message.send",
        payload={"text": "hello"},
    )

    assert command.can_transition_to(CommandStatus.DISPATCHED) is True
    assert command.can_transition_to(CommandStatus.SUCCEEDED) is False


@pytest.mark.django_db
def test_claude_code_local_command_is_accepted_by_api(
    authenticated_api_client: APIClient,
) -> None:
    agent = Agent.objects.create(name="agent")

    response = authenticated_api_client.post(
        "/api/v1/commands/",
        {
            "agent_id": str(agent.id),
            "provider": "claude_code_local",
            "action": "claude_code_local.session.status",
            "payload": {},
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["provider"] == "claude_code_local"
    assert response.json()["action"] == "claude_code_local.session.status"


@pytest.mark.django_db
def test_invalid_action_for_provider_is_rejected(
    authenticated_api_client: APIClient,
) -> None:
    agent = Agent.objects.create(name="agent")

    response = authenticated_api_client.post(
        "/api/v1/commands/",
        {
            "agent_id": str(agent.id),
            "provider": "claude_code_local",
            "action": "echo.message.send",
            "payload": {"text": "hello"},
        },
        format="json",
    )

    assert response.status_code == 400
    assert "action" in response.json()


@pytest.mark.django_db
def test_windows_powershell_command_is_accepted_by_api(
    authenticated_api_client: APIClient,
) -> None:
    agent = Agent.objects.create(name="agent")

    response = authenticated_api_client.post(
        "/api/v1/commands/",
        {
            "agent_id": str(agent.id),
            "provider": "windows_powershell",
            "action": "windows_powershell.system.info",
            "payload": {},
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["provider"] == "windows_powershell"
    assert response.json()["action"] == "windows_powershell.system.info"


@pytest.mark.django_db
def test_windows_powershell_persistent_session_command_is_accepted_by_api(
    authenticated_api_client: APIClient,
) -> None:
    agent = Agent.objects.create(name="agent")

    response = authenticated_api_client.post(
        "/api/v1/commands/",
        {
            "agent_id": str(agent.id),
            "provider": "windows_powershell",
            "action": "windows_powershell.session.create",
            "payload": {"working_directory": "C:\\Users"},
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["action"] == "windows_powershell.session.create"


@pytest.mark.django_db
def test_windows_powershell_terminal_command_is_accepted_by_api(
    authenticated_api_client: APIClient,
) -> None:
    agent = Agent.objects.create(name="agent")

    response = authenticated_api_client.post(
        "/api/v1/commands/",
        {
            "agent_id": str(agent.id),
            "provider": "windows_powershell",
            "action": "windows_powershell.command.run",
            "payload": {
                "session_id": "11111111-1111-1111-1111-111111111111",
                "command": "Get-Location",
            },
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["action"] == "windows_powershell.command.run"


@pytest.mark.django_db
def test_dispatch_command_allows_connected_agent_in_error_state() -> None:
    agent = Agent.objects.create(name="agent", status="error")
    command = Command.objects.create(
        agent=agent,
        provider="windows_powershell",
        action="windows_powershell.system.info",
        payload={},
    )

    dispatch_command(command)
    command.refresh_from_db()

    assert command.status == CommandStatus.DISPATCHED


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_command_request_can_complete_with_progress() -> None:
    agent = await Agent.objects.acreate(name="agent")
    token = await sync_to_async(agent.issue_development_token)()
    communicator = WebsocketCommunicator(
        application,
        f"/ws/v1/agent/?agent_id={agent.id}&token={token}",
    )
    connected, _ = await communicator.connect()
    assert connected is True
    await communicator.receive_json_from()

    await communicator.send_json_to(
        build_message(
            message_type="agent.hello",
            correlation_id=str(agent.id),
            payload={"version": "0.1.0", "operating_system": "Windows"},
        )
    )

    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    user = await user_model.objects.acreate_user(
        username="commands-user-1",
        password="commands-password",
    )
    client = APIClient()
    client.force_authenticate(user=user)
    response = await sync_to_async(client.post)(
        "/api/v1/commands/",
        {
            "agent_id": str(agent.id),
            "provider": "echo",
            "action": "echo.message.send",
            "payload": {"text": "hello world"},
        },
        format="json",
    )
    assert response.status_code == 201
    command_id = response.json()["id"]

    outbound = await communicator.receive_json_from()
    assert outbound["type"] == "command.request"

    correlation_id = outbound["correlation_id"]
    await communicator.send_json_to(
        build_message(
            message_type="command.started",
            correlation_id=correlation_id,
            payload={"command_id": command_id},
        )
    )
    await communicator.send_json_to(
        build_message(
            message_type="command.progress",
            correlation_id=correlation_id,
            payload={
                "command_id": command_id,
                "event": "message.delta",
                "sequence": 1,
                "delta": "hello ",
            },
        )
    )
    await communicator.send_json_to(
        build_message(
            message_type="command.completed",
            correlation_id=correlation_id,
            payload={
                "command_id": command_id,
                "result": {"text": "hello world"},
            },
        )
    )

    command: Command | None = None
    for _ in range(10):
        command = (
            await Command.objects.select_related("agent")
            .prefetch_related("events")
            .aget(id=command_id)
        )
        if command.status == CommandStatus.SUCCEEDED:
            break
        await asyncio.sleep(0.05)

    assert command is not None
    assert command.status == CommandStatus.SUCCEEDED
    assert command.result == {"text": "hello world"}
    assert await command.events.acount() == 1

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_claude_code_local_status_command_can_complete_without_progress() -> None:
    agent = await Agent.objects.acreate(name="agent")
    token = await sync_to_async(agent.issue_development_token)()
    communicator = WebsocketCommunicator(
        application,
        f"/ws/v1/agent/?agent_id={agent.id}&token={token}",
    )
    connected, _ = await communicator.connect()
    assert connected is True
    await communicator.receive_json_from()

    await communicator.send_json_to(
        build_message(
            message_type="agent.hello",
            correlation_id=str(agent.id),
            payload={"version": "0.1.0", "operating_system": "Windows"},
        )
    )

    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    user = await user_model.objects.acreate_user(
        username="commands-user-2",
        password="commands-password",
    )
    client = APIClient()
    client.force_authenticate(user=user)
    response = await sync_to_async(client.post)(
        "/api/v1/commands/",
        {
            "agent_id": str(agent.id),
            "provider": "claude_code_local",
            "action": "claude_code_local.session.status",
            "payload": {},
        },
        format="json",
    )
    assert response.status_code == 201
    command_id = response.json()["id"]

    outbound = await communicator.receive_json_from()
    assert outbound["type"] == "command.request"
    assert outbound["payload"]["action"] == "claude_code_local.session.status"

    correlation_id = outbound["correlation_id"]
    await communicator.send_json_to(
        build_message(
            message_type="command.started",
            correlation_id=correlation_id,
            payload={"command_id": command_id},
        )
    )
    await communicator.send_json_to(
        build_message(
            message_type="command.completed",
            correlation_id=correlation_id,
            payload={
                "command_id": command_id,
                "result": {
                    "provider": "claude_code_local",
                    "session_detected": False,
                    "session_ready": False,
                    "manual_action_required": True,
                    "transport": "unknown",
                    "action_required": {
                        "type": "local_session_setup",
                        "provider": "claude_code_local",
                        "message": "Claude Code CLI was not found.",
                    },
                },
            },
        )
    )

    command: Command | None = None
    for _ in range(10):
        command = (
            await Command.objects.select_related("agent")
            .prefetch_related("events")
            .aget(id=command_id)
        )
        if command.status == CommandStatus.SUCCEEDED:
            break
        await asyncio.sleep(0.05)

    assert command is not None
    assert command.status == CommandStatus.SUCCEEDED
    assert command.result["provider"] == "claude_code_local"
    assert await command.events.acount() == 0

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_command_failed_message_marks_command_failed() -> None:
    agent = await Agent.objects.acreate(name="agent")
    token = await sync_to_async(agent.issue_development_token)()
    communicator = WebsocketCommunicator(
        application,
        f"/ws/v1/agent/?agent_id={agent.id}&token={token}",
    )
    connected, _ = await communicator.connect()
    assert connected is True
    await communicator.receive_json_from()

    await communicator.send_json_to(
        build_message(
            message_type="agent.hello",
            correlation_id=str(agent.id),
            payload={"version": "0.1.0", "operating_system": "Windows"},
        )
    )

    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    user = await user_model.objects.acreate_user(
        username="commands-user-3",
        password="commands-password",
    )
    client = APIClient()
    client.force_authenticate(user=user)
    response = await sync_to_async(client.post)(
        "/api/v1/commands/",
        {
            "agent_id": str(agent.id),
            "provider": "claude_code_local",
            "action": "claude_code_local.message.send",
            "payload": {"text": "hello"},
        },
        format="json",
    )
    assert response.status_code == 201
    command_id = response.json()["id"]

    outbound = await communicator.receive_json_from()
    assert outbound["type"] == "command.request"
    correlation_id = outbound["correlation_id"]

    await communicator.send_json_to(
        build_message(
            message_type="command.started",
            correlation_id=correlation_id,
            payload={"command_id": command_id},
        )
    )
    await communicator.send_json_to(
        build_message(
            message_type="command.failed",
            correlation_id=correlation_id,
            payload={"command_id": command_id, "error": "Claude Code CLI failed"},
        )
    )

    command: Command | None = None
    for _ in range(10):
        command = await Command.objects.aget(id=command_id)
        if command.status == CommandStatus.FAILED:
            break
        await asyncio.sleep(0.05)

    assert command is not None
    assert command.status == CommandStatus.FAILED
    assert command.error == "Claude Code CLI failed"

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_frontend_events_socket_receives_command_updates_and_progress() -> None:
    agent = await Agent.objects.acreate(name="agent")
    from django.contrib.auth import get_user_model
    from rest_framework.authtoken.models import Token

    user_model = get_user_model()
    user = await user_model.objects.acreate_user(
        username="commands-user-4",
        password="commands-password",
    )
    user_token = await Token.objects.acreate(user=user)

    token = await sync_to_async(agent.issue_development_token)()
    event_communicator = WebsocketCommunicator(
        application, f"/ws/v1/events/?token={user_token.key}"
    )
    event_connected, _ = await event_communicator.connect()
    assert event_connected is True

    communicator = WebsocketCommunicator(
        application,
        f"/ws/v1/agent/?agent_id={agent.id}&token={token}",
    )
    connected, _ = await communicator.connect()
    assert connected is True
    await communicator.receive_json_from()

    await communicator.send_json_to(
        build_message(
            message_type="agent.hello",
            correlation_id=str(agent.id),
            payload={"version": "0.1.0", "operating_system": "Windows"},
        )
    )
    await _receive_event_type(event_communicator, "agent.updated")

    client = APIClient()
    client.force_authenticate(user=user)
    response = await sync_to_async(client.post)(
        "/api/v1/commands/",
        {
            "agent_id": str(agent.id),
            "provider": "echo",
            "action": "echo.message.send",
            "payload": {"text": "hello world"},
        },
        format="json",
    )
    assert response.status_code == 201
    command_id = response.json()["id"]

    command_dispatched = await _receive_event_type(
        event_communicator, "command.updated"
    )
    assert command_dispatched["type"] == "command.updated"
    assert command_dispatched["payload"]["command"]["id"] == command_id
    assert (
        command_dispatched["payload"]["command"]["status"]
        == CommandStatus.DISPATCHED
    )

    outbound = await communicator.receive_json_from()
    correlation_id = outbound["correlation_id"]

    await communicator.send_json_to(
        build_message(
            message_type="command.started",
            correlation_id=correlation_id,
            payload={"command_id": command_id},
        )
    )
    command_started = await _receive_event_type(event_communicator, "command.updated")
    assert command_started["type"] == "command.updated"
    assert command_started["payload"]["command"]["status"] == CommandStatus.RUNNING

    await communicator.send_json_to(
        build_message(
            message_type="command.progress",
            correlation_id=correlation_id,
            payload={
                "command_id": command_id,
                "event": "message.delta",
                "sequence": 1,
                "delta": "hello ",
            },
        )
    )
    progress_event = await _receive_event_type(event_communicator, "command.event")
    assert progress_event["type"] == "command.event"
    assert progress_event["payload"]["command_id"] == command_id
    assert progress_event["payload"]["event"]["sequence"] == 1

    await communicator.disconnect()
    await event_communicator.disconnect()


@pytest.mark.django_db
def test_cancel_pending_command_marks_it_cancelled(
    authenticated_api_client: APIClient,
    api_user: Any,
) -> None:
    agent = Agent.objects.create(name="agent")
    command = Command.objects.create(
        requested_by=api_user,
        agent=agent,
        provider="windows_powershell",
        action="windows_powershell.command.run",
        payload={"session_id": "abc", "command": "Get-Location"},
    )

    response = authenticated_api_client.post(f"/api/v1/commands/{command.id}/cancel/")

    assert response.status_code == 200
    command.refresh_from_db()
    assert command.status == CommandStatus.CANCELLED
    assert command.error == "Command cancelled"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_cancel_running_command_marks_it_cancelled() -> None:
    agent = await Agent.objects.acreate(name="agent")
    token = await sync_to_async(agent.issue_development_token)()
    communicator = WebsocketCommunicator(
        application,
        f"/ws/v1/agent/?agent_id={agent.id}&token={token}",
    )
    connected, _ = await communicator.connect()
    assert connected is True
    await communicator.receive_json_from()

    await communicator.send_json_to(
        build_message(
            message_type="agent.hello",
            correlation_id=str(agent.id),
            payload={"version": "0.1.0", "operating_system": "Windows"},
        )
    )

    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    user = await user_model.objects.acreate_user(
        username="commands-user-cancel",
        password="commands-password",
    )
    client = APIClient()
    client.force_authenticate(user=user)
    response = await sync_to_async(client.post)(
        "/api/v1/commands/",
        {
            "agent_id": str(agent.id),
            "provider": "windows_powershell",
            "action": "windows_powershell.command.run",
            "payload": {
                "session_id": "11111111-1111-1111-1111-111111111111",
                "command": "Get-Location",
            },
        },
        format="json",
    )
    assert response.status_code == 201
    command_id = response.json()["id"]

    outbound = await communicator.receive_json_from()
    assert outbound["type"] == "command.request"
    correlation_id = outbound["correlation_id"]

    await communicator.send_json_to(
        build_message(
            message_type="command.started",
            correlation_id=correlation_id,
            payload={"command_id": command_id},
        )
    )

    cancel_response = await sync_to_async(client.post)(
        f"/api/v1/commands/{command_id}/cancel/",
        {},
        format="json",
    )
    assert cancel_response.status_code == 202

    cancel_outbound = await communicator.receive_json_from()
    assert cancel_outbound["type"] == "command.cancel"
    assert cancel_outbound["payload"]["command_id"] == command_id

    await communicator.send_json_to(
        build_message(
            message_type="command.failed",
            correlation_id=correlation_id,
            payload={
                "command_id": command_id,
                "error": "Command cancelled",
                "cancelled": True,
            },
        )
    )

    command: Command | None = None
    for _ in range(10):
        command = await Command.objects.aget(id=command_id)
        if command.status == CommandStatus.CANCELLED:
            break
        await asyncio.sleep(0.05)

    assert command is not None
    assert command.status == CommandStatus.CANCELLED

    await communicator.disconnect()

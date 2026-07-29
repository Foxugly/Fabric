from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

PROTOCOL_VERSION = "1.0"
SUPPORTED_TYPES = {
    "agent.hello",
    "agent.authenticated",
    "agent.heartbeat",
    "agent.status",
    "agent.updated",
    "provider.capabilities",
    "command.request",
    "command.accepted",
    "command.started",
    "command.progress",
    "command.completed",
    "command.failed",
    "command.cancel",
    "command.updated",
    "command.event",
    "session.action_required",
    "session.action_response",
    "command.permission_request",
    "error",
}


class ProtocolValidationError(ValueError):
    """Raised when a protocol message is invalid."""


@dataclass(slots=True, frozen=True)
class ProtocolEnvelope:
    protocol_version: str
    type: str
    message_id: str
    correlation_id: str
    timestamp: str
    payload: dict[str, Any]


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_message(
    *,
    message_type: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
    message_id: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    if message_type not in SUPPORTED_TYPES:
        raise ProtocolValidationError(f"Unsupported message type: {message_type}")

    envelope = {
        "protocol_version": PROTOCOL_VERSION,
        "type": message_type,
        "message_id": message_id or str(uuid4()),
        "correlation_id": correlation_id or str(uuid4()),
        "timestamp": timestamp or utcnow_iso(),
        "payload": payload,
    }
    parse_message(envelope)
    return envelope


def parse_message(data: dict[str, Any]) -> ProtocolEnvelope:
    required_fields = {
        "protocol_version": str,
        "type": str,
        "message_id": str,
        "correlation_id": str,
        "timestamp": str,
        "payload": dict,
    }
    for field_name, expected_type in required_fields.items():
        if field_name not in data:
            raise ProtocolValidationError(f"Missing field: {field_name}")
        if not isinstance(data[field_name], expected_type):
            raise ProtocolValidationError(
                f"Field {field_name} must be {expected_type.__name__}"
            )

    if data["protocol_version"] != PROTOCOL_VERSION:
        raise ProtocolValidationError(
            f"Unsupported protocol version: {data['protocol_version']}"
        )
    if data["type"] not in SUPPORTED_TYPES:
        raise ProtocolValidationError(f"Unsupported message type: {data['type']}")

    _validate_uuid_string(data["message_id"], "message_id")
    _validate_uuid_string(data["correlation_id"], "correlation_id")
    _validate_timestamp(data["timestamp"])

    return ProtocolEnvelope(
        protocol_version=data["protocol_version"],
        type=data["type"],
        message_id=data["message_id"],
        correlation_id=data["correlation_id"],
        timestamp=data["timestamp"],
        payload=data["payload"],
    )


def _validate_uuid_string(value: str, field_name: str) -> None:
    try:
        UUID(value)
    except ValueError as exc:
        raise ProtocolValidationError(f"Invalid UUID for {field_name}") from exc


def _validate_timestamp(value: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtocolValidationError("Invalid ISO-8601 timestamp") from exc

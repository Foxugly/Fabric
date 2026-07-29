from __future__ import annotations

import pytest
from shared.protocol import ProtocolValidationError, build_message, parse_message


def test_protocol_message_roundtrip() -> None:
    message = build_message(message_type="agent.heartbeat", payload={})
    envelope = parse_message(message)

    assert envelope.type == "agent.heartbeat"
    assert envelope.payload == {}


def test_protocol_rejects_invalid_version() -> None:
    with pytest.raises(ProtocolValidationError):
        parse_message(
            {
                "protocol_version": "2.0",
                "type": "agent.heartbeat",
                "message_id": "c7d4eb9f-9f61-45c4-8634-8d59955c3760",
                "correlation_id": "c7d4eb9f-9f61-45c4-8634-8d59955c3760",
                "timestamp": "2026-07-29T09:30:00+00:00",
                "payload": {},
            }
        )

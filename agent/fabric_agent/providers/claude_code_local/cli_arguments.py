"""Validation of the payload values that end up on the `claude` command line.

The payload comes from the WebSocket, so it is untrusted (see `policy.py`). A
value starting with `-` would be read by the CLI as an option of its own:
`disallowed_tools: ["--dangerously-skip-permissions"]` or the same string as a
`session_id` would switch every permission check off. Every value is therefore
checked against a strict pattern here, and the runner also glues it to its
option (`--resume=<id>`), so the CLI never parses it as an option.
"""

from __future__ import annotations

import re
from typing import Any

#: Keys a `claude_code_local.message.send` payload may carry.
MESSAGE_PAYLOAD_KEYS = frozenset(
    {
        "text",
        "prompt",
        "session_id",
        "working_directory",
        "timeout_seconds",
        "permission_mode",
        "model",
        "allowed_tools",
        "disallowed_tools",
        "permission_prompt",
    }
)

#: Claude Code session ids are UUIDs.
_SESSION_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
#: Aliases (`sonnet`), full ids (`claude-opus-4-1`, `...[1m]`) and Bedrock ids.
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,127}")
#: Tool names and rules: `Bash`, `mcp__server__tool`, `Bash(git log:*)`,
#: `Read(./src/**)`. No comma: each tool is passed as its own argument.
_TOOL = re.compile(r"[A-Za-z0-9_*][A-Za-z0-9_*:().\-/~ ]{0,255}")
MAX_TOOLS = 64


def _not_an_option(value: str, field_name: str) -> None:
    if value.startswith("-"):
        raise ValueError(f"{field_name} must not start with '-'")


def session_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SESSION_ID.fullmatch(value):
        raise ValueError("session_id must be a UUID")
    return value


def model(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("model must be a non-empty string when provided")
    _not_an_option(value, "model")
    if not _MODEL.fullmatch(value):
        raise ValueError("model contains unsupported characters")
    return value


def tool_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in value
    ):
        raise ValueError(f"{field_name} must be a list of non-empty strings")
    if len(value) > MAX_TOOLS:
        raise ValueError(f"{field_name} accepts at most {MAX_TOOLS} entries")
    for entry in value:
        _not_an_option(entry, field_name)
        if not _TOOL.fullmatch(entry):
            raise ValueError(f"{field_name} contains an unsupported tool name")
    return tuple(value)


def unknown_message_keys(payload: dict[str, Any]) -> list[str]:
    return sorted(str(key) for key in payload if key not in MESSAGE_PAYLOAD_KEYS)

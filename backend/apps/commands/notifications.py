"""Push notifications through PushIT, the fleet's own notification service.

Why this exists: Fabric is driven from a phone, away from the screen. A Claude
turn blocked on an approval is invisible until you happen to look, and it will
sit there until the turn times out. A push turns the approval bridge from
"stay in front of the browser" into "answer when it buzzes".

Two rules govern everything here:

- **A notification must never affect the command it describes.** Every send runs
  on a daemon thread with a short timeout, and failures are logged, never
  raised. PushIT being down must not fail a deploy, a turn, or an approval.
- **Nothing is sent unless explicitly switched on** (`PUSHIT_ENABLED` plus a
  token). A credential lying around is not consent — the same rule that keeps
  dev crashes out of the production Sentry project.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings

from apps.commands.models import Command, CommandStatus, PermissionRequest

LOGGER = logging.getLogger(__name__)

SEND_PATH = "/api/v1/notifications/app/send/"
MESSAGE_MAX_CHARS = 300


def notify_permission_request(
    command: Command,
    request: PermissionRequest,
) -> None:
    """Claude is blocked until a human rules on this tool call."""
    hint = _tool_hint(request.tool_input)
    _send(
        event="permission_request",
        idempotency_key=f"fabric:perm:{request.request_id}",
        title="Fabric — autorisation demandée",
        message=(
            f"Claude veut utiliser {request.tool_name}{hint}. "
            "Répondez allow ou deny dans le terminal."
        ),
    )


def notify_command_finished(command: Command) -> None:
    """A Claude turn reached a final state.

    Scoped to `claude_code_local`: PowerShell commands are short and watched
    live, so notifying on those would be noise.
    """
    if command.provider != "claude_code_local":
        return

    if command.status == CommandStatus.SUCCEEDED:
        event, title = "claude_turn_completed", "Fabric — tour terminé"
        body = _result_excerpt(command) or "Terminé sans texte."
    else:
        event, title = "claude_turn_failed", f"Fabric — tour {command.status}"
        body = command.error or "Sans détail."

    _send(
        event=event,
        idempotency_key=f"fabric:cmd:{command.id}:{command.status}",
        title=title,
        message=body,
    )


def notify_agent_offline(agent_name: str, in_flight: int) -> None:
    _send(
        event="agent_offline",
        idempotency_key=f"fabric:offline:{agent_name}:{in_flight}",
        title="Fabric — agent déconnecté",
        message=(
            f"L'agent {agent_name} s'est déconnecté "
            f"avec {in_flight} commande(s) en cours."
        ),
    )


def _send(*, event: str, idempotency_key: str, title: str, message: str) -> None:
    if not getattr(settings, "PUSHIT_ACTIVE", False):
        return
    if not settings.PUSHIT_EVENTS.get(event, False):
        return

    payload = json.dumps(
        {"title": title[:255], "message": _truncate(message)}
    ).encode("utf-8")

    thread = threading.Thread(
        target=_post,
        args=(payload, idempotency_key, event),
        name=f"pushit-{event}",
        daemon=True,
    )
    thread.start()


def _post(payload: bytes, idempotency_key: str, event: str) -> None:
    url = f"{settings.PUSHIT_BASE_URL}{SEND_PATH}"
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-App-Token": settings.PUSHIT_APP_TOKEN,
            # Required by the endpoint; replaying a key returns the existing
            # notification instead of sending twice.
            "Idempotency-Key": idempotency_key,
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=settings.PUSHIT_TIMEOUT_SECONDS
        ) as response:
            LOGGER.info("PushIT %s -> HTTP %s", event, response.status)
    except urllib.error.HTTPError as exc:
        LOGGER.warning("PushIT %s refused: HTTP %s %s", event, exc.code, exc.reason)
    except Exception as exc:  # noqa: BLE001 - a notification never breaks a command
        LOGGER.warning("PushIT %s failed: %s", event, exc)


def _truncate(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= MESSAGE_MAX_CHARS:
        return collapsed
    return collapsed[: MESSAGE_MAX_CHARS - 1] + "…"


def _result_excerpt(command: Command) -> str:
    text = command.result.get("text")
    return _truncate(text) if isinstance(text, str) else ""


def _tool_hint(tool_input: Any) -> str:
    """The argument that makes a permission request decidable at a glance."""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "file_path", "path", "pattern", "url"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return f" — {value.strip().splitlines()[0][:120]}"
    return ""

"""Push notifications through PushIT, the fleet's own notification service.

Why this exists: Fabric is driven from a phone, away from the screen. A Claude
turn blocked on an approval is invisible until you happen to look, and it will
sit there until the turn times out. A push turns the approval bridge from
"stay in front of the browser" into "answer when it buzzes".

Two rules govern everything here:

- **A notification must never affect the command it describes.** Every send runs
  on a daemon thread with a short timeout, and failures are logged, never
  raised. PushIT being down must not fail a deploy, a turn, or an approval.
- **No content leaves Fabric** (I4): a notification names the event, the tool
  and the status — never a result excerpt, a tool argument or an error text.
  Those can hold secrets (`Get-Content .env`), and a push lands on a phone
  lock screen and in PushIT's own database.
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
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from apps.api_auth.pushit_urls import pushit_url_error
from apps.commands.models import Command, CommandStatus, PermissionRequest

LOGGER = logging.getLogger(__name__)

SEND_PATH = "/api/v1/notifications/app/send/"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the app token must not travel past the
    allow-listed host (a 3xx surfaces as an HTTPError instead)."""

    def redirect_request(
        self, *args: Any, **kwargs: Any
    ) -> urllib.request.Request | None:
        return None


#: HTTPS only, no redirects: the scheme and host are checked before each send.
_OPENER = urllib.request.build_opener(_NoRedirect)
MESSAGE_MAX_CHARS = 300


@dataclass(frozen=True, slots=True)
class Destination:
    """Where a notification goes: a token, a host, and a title."""

    app_token: str
    base_url: str
    title_prefix: str
    policy: dict[str, bool]

    def wants(self, event: str) -> bool:
        return bool(self.policy.get(event, False))


def _destination_for(user: Any) -> Destination | None:
    """The user's own PushIT target, falling back to the site settings.

    Per-user first: with owners in the picture, "who to notify" is user data.
    The site-level PUSHIT_* settings remain a fallback so a deployment without
    any profile configured still behaves.
    """
    if user is not None:
        from apps.api_auth.models import PushItTarget

        target = (
            PushItTarget.objects.filter(owner=user, enabled=True)
            .order_by("-is_default", "name")
            .first()
        )
        if target is not None and target.app_token:
            return Destination(
                app_token=target.app_token,
                base_url=target.base_url.rstrip("/"),
                title_prefix=target.title or "Fabric",
                policy=target.event_policy(),
            )

    if getattr(settings, "PUSHIT_ACTIVE", False):
        return Destination(
            app_token=settings.PUSHIT_APP_TOKEN,
            base_url=settings.PUSHIT_BASE_URL.rstrip("/"),
            title_prefix="Fabric",
            policy=dict(settings.PUSHIT_EVENTS),
        )
    return None


def notify_permission_request(
    command: Command,
    request: PermissionRequest,
) -> None:
    """Claude is blocked until a human rules on this tool call.

    Only the tool's name: its argument (a command line, a path, a URL) stays in
    Fabric, where the operator reads it before deciding.
    """
    _send(
        destination=_destination_for(command.requested_by),
        event="permission_request",
        idempotency_key=f"fabric:perm:{request.request_id}",
        title="autorisation demandée",
        message=(
            f"Claude veut utiliser {request.tool_name[:64]}. "
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
        event, title = "claude_turn_completed", "tour terminé"
        body = "Le tour Claude est terminé. Le résultat est dans Fabric."
    else:
        event, title = "claude_turn_failed", f"tour {command.status}"
        body = f"Le tour Claude s'est terminé en {command.status}. Détails dans Fabric."

    _send(
        destination=_destination_for(command.requested_by),
        event=event,
        idempotency_key=f"fabric:cmd:{command.id}:{command.status}",
        title=title,
        message=body,
    )


def notify_agent_offline(agent: Any, in_flight: int) -> None:
    _send(
        destination=_destination_for(getattr(agent, "owner", None)),
        event="agent_offline",
        idempotency_key=f"fabric:offline:{agent.name}:{in_flight}",
        title="agent déconnecté",
        message=(
            f"L'agent {agent.name} s'est déconnecté "
            f"avec {in_flight} commande(s) en cours."
        ),
    )


def _send(
    *,
    destination: Destination | None,
    event: str,
    idempotency_key: str,
    title: str,
    message: str,
) -> None:
    if destination is None or not destination.wants(event):
        return

    payload = json.dumps(
        {
            "title": f"{destination.title_prefix} — {title}"[:255],
            "message": _truncate(message),
        }
    ).encode("utf-8")

    thread = threading.Thread(
        target=_post,
        args=(destination, payload, idempotency_key, event),
        name=f"pushit-{event}",
        daemon=True,
    )
    thread.start()


def _post(
    destination: Destination,
    payload: bytes,
    idempotency_key: str,
    event: str,
) -> None:
    error = pushit_url_error(destination.base_url)
    if error is not None:
        # Rows saved before the allow-list existed, or a mistyped site setting:
        # refuse rather than POST the token somewhere unexpected.
        LOGGER.warning("PushIT %s not sent: %s", event, error)
        return
    url = f"{destination.base_url.rstrip('/')}{SEND_PATH}"
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-App-Token": destination.app_token,
            # Required by the endpoint; replaying a key returns the existing
            # notification instead of sending twice.
            "Idempotency-Key": idempotency_key,
        },
    )
    try:
        with _OPENER.open(
            request, timeout=getattr(settings, "PUSHIT_TIMEOUT_SECONDS", 5)
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


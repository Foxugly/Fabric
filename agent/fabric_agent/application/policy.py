"""Local guard-rails of the agent, decided on the PC and never by the server.

Everything that arrives on the WebSocket is untrusted: a stolen agent token, a
compromised Fabric server or anyone able to write into its Redis channel layer
can forge a `command.request`. The approval bridge only protects against Claude
itself, so the dangerous switches (raw PowerShell, `bypassPermissions`, turning
the approval bridge off, rewriting an approved tool input, cutting the network)
are owned by a TOML file on this machine.

The file lives at `%APPDATA%\\fabric-agent\\policy.toml` (or
`~/.config/fabric-agent/policy.toml` elsewhere), overridable with
`FABRIC_AGENT_POLICY_FILE`. Without it the agent runs with the most restrictive
defaults below. A file that cannot be parsed stops the agent instead of being
silently replaced by defaults, so a typo is noticed.

Example (every key is optional)::

    # Qualified actions this PC accepts (default: the whole catalogue).
    allowed_actions = ["claude_code_local.message.send", "echo.message.send"]
    # Directories a command may run in; empty means no restriction.
    working_directories = ["D:/Projects"]

    [claude_code_local]
    allowed_permission_modes = ["default", "plan"]
    allow_disable_approval = false   # payload `permission_prompt: false`
    allow_preapproved_tools = false  # payload `allowed_tools`
    allow_updated_input = false      # approval rewriting the tool input

    [windows_powershell]
    allow_raw_commands = false       # `command.run` with a free `command`
    allow_network_recover = false    # remote `network.recover`
    network_recover_adapters = []    # adapters it may disable/enable

    [approvals]
    require_fingerprint = false      # approvals must echo the request fingerprint
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.protocol import all_qualified_actions, local_action, qualify

LOGGER = logging.getLogger(__name__)

POLICY_FILE_ENV = "FABRIC_AGENT_POLICY_FILE"
POLICY_FILE_NAME = "policy.toml"

#: Modes that keep every tool call behind the approval bridge.
DEFAULT_PERMISSION_MODES = frozenset({"default", "plan"})

_TOP_LEVEL_KEYS = {
    "allowed_actions",
    "working_directories",
    "claude_code_local",
    "windows_powershell",
    "approvals",
}
_CLAUDE_KEYS = {
    "allowed_permission_modes",
    "allow_disable_approval",
    "allow_preapproved_tools",
    "allow_updated_input",
}
_POWERSHELL_KEYS = {
    "allow_raw_commands",
    "allow_network_recover",
    "network_recover_adapters",
}
_APPROVAL_KEYS = {"require_fingerprint"}


class PolicyError(RuntimeError):
    """The local policy file is missing required structure or is malformed."""


class PolicyViolation(PermissionError):
    """A server request asks for something the local policy forbids."""


def default_policy_path() -> Path:
    configured = os.environ.get(POLICY_FILE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "fabric-agent" / POLICY_FILE_NAME
    return Path.home() / ".config" / "fabric-agent" / POLICY_FILE_NAME


@dataclass(slots=True, frozen=True)
class LocalPolicy:
    allowed_actions: frozenset[str] = field(
        default_factory=lambda: frozenset(all_qualified_actions())
    )
    working_directories: tuple[Path, ...] = ()
    allowed_permission_modes: frozenset[str] = DEFAULT_PERMISSION_MODES
    allow_disable_approval: bool = False
    allow_preapproved_tools: bool = False
    allow_updated_input: bool = False
    allow_raw_powershell: bool = False
    allow_network_recover: bool = False
    network_recover_adapters: frozenset[str] = frozenset()
    require_approval_fingerprint: bool = False
    source: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> LocalPolicy:
        policy_path = path or default_policy_path()
        if not policy_path.is_file():
            LOGGER.warning(
                "No local policy at %s: using the restrictive defaults "
                "(raw PowerShell off, bypassPermissions refused, "
                "network.recover off, every tool call needs approval)",
                policy_path,
            )
            return cls()
        try:
            with policy_path.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise PolicyError(f"Cannot read local policy {policy_path}: {exc}") from exc
        policy = cls.from_mapping(data, source=policy_path)
        policy.log_relaxations()
        return policy

    @classmethod
    def from_mapping(
        cls, data: dict[str, Any], source: Path | None = None
    ) -> LocalPolicy:
        _reject_unknown(data, _TOP_LEVEL_KEYS, "policy")
        claude = _section(data, "claude_code_local", _CLAUDE_KEYS)
        powershell = _section(data, "windows_powershell", _POWERSHELL_KEYS)
        approvals = _section(data, "approvals", _APPROVAL_KEYS)

        catalogue = all_qualified_actions()
        allowed_actions = frozenset(
            _string_list(data, "allowed_actions", default=sorted(catalogue))
        )
        unknown_actions = allowed_actions - catalogue
        if unknown_actions:
            raise PolicyError(
                "Unknown actions in allowed_actions: "
                + ", ".join(sorted(unknown_actions))
            )

        return cls(
            allowed_actions=allowed_actions,
            working_directories=tuple(
                Path(entry).expanduser()
                for entry in _string_list(data, "working_directories", default=[])
            ),
            allowed_permission_modes=frozenset(
                _string_list(
                    claude,
                    "allowed_permission_modes",
                    default=sorted(DEFAULT_PERMISSION_MODES),
                )
            ),
            allow_disable_approval=_bool(claude, "allow_disable_approval"),
            allow_preapproved_tools=_bool(claude, "allow_preapproved_tools"),
            allow_updated_input=_bool(claude, "allow_updated_input"),
            allow_raw_powershell=_bool(powershell, "allow_raw_commands"),
            allow_network_recover=_bool(powershell, "allow_network_recover"),
            network_recover_adapters=frozenset(
                _string_list(powershell, "network_recover_adapters", default=[])
            ),
            require_approval_fingerprint=_bool(approvals, "require_fingerprint"),
            source=source,
        )

    def log_relaxations(self) -> None:
        """Make every loosened guard-rail visible in the agent log."""
        relaxed = [
            name
            for name, enabled in (
                ("raw PowerShell commands", self.allow_raw_powershell),
                ("remote network.recover", self.allow_network_recover),
                ("permission_prompt: false", self.allow_disable_approval),
                ("server-side allowed_tools", self.allow_preapproved_tools),
                ("server-side updated_input", self.allow_updated_input),
                (
                    "bypassPermissions",
                    "bypassPermissions" in self.allowed_permission_modes,
                ),
            )
            if enabled
        ]
        if relaxed:
            LOGGER.warning(
                "Local policy %s relaxes: %s", self.source, ", ".join(relaxed)
            )

    def authorize(
        self, provider: str, action: str, parameters: dict[str, Any]
    ) -> None:
        """Raise `PolicyViolation` unless this PC accepts the request as sent."""
        local = local_action(provider, action)
        qualified = qualify(provider, local)
        if qualified not in self.allowed_actions:
            raise PolicyViolation(f"action {qualified} is not allowed on this PC")

        self._check_working_directory(parameters.get("working_directory"))

        if provider == "claude_code_local" and local == "message.send":
            self._check_claude_message(parameters)
        elif provider == "windows_powershell":
            self._check_powershell(local, parameters)

    def _check_claude_message(self, parameters: dict[str, Any]) -> None:
        mode = parameters.get("permission_mode")
        if mode is not None and (
            not isinstance(mode, str) or mode not in self.allowed_permission_modes
        ):
            raise PolicyViolation(
                f"permission_mode {mode!r} is not allowed on this PC "
                f"(allowed: {', '.join(sorted(self.allowed_permission_modes))})"
            )
        if parameters.get("permission_prompt") is False and (
            not self.allow_disable_approval
        ):
            raise PolicyViolation(
                "turning the approval bridge off (permission_prompt: false) "
                "is not allowed on this PC"
            )
        if parameters.get("allowed_tools") and not self.allow_preapproved_tools:
            raise PolicyViolation(
                "pre-approved tools (allowed_tools) are not allowed on this PC: "
                "every tool call must be approved"
            )

    def _check_powershell(self, action: str, parameters: dict[str, Any]) -> None:
        if action == "command.run":
            command = parameters.get("command")
            if isinstance(command, str) and command.strip():
                if not self.allow_raw_powershell:
                    raise PolicyViolation(
                        "raw PowerShell commands are disabled on this PC"
                    )
        elif action == "network.recover":
            if not self.allow_network_recover:
                raise PolicyViolation("network.recover is disabled on this PC")
            adapter = parameters.get("adapter_name")
            if adapter is not None and (
                not isinstance(adapter, str)
                or adapter not in self.network_recover_adapters
            ):
                raise PolicyViolation(
                    f"network adapter {adapter!r} is not allowed on this PC"
                )

    def _check_working_directory(self, value: Any) -> None:
        if value is None or not self.working_directories:
            return
        if not isinstance(value, str) or not value.strip():
            raise PolicyViolation("working_directory must be a non-empty string")
        candidate = Path(value).expanduser().resolve()
        for root in self.working_directories:
            if candidate.is_relative_to(root.resolve()):
                return
        raise PolicyViolation(
            f"working_directory {value!r} is outside the allowed directories"
        )


def _section(data: dict[str, Any], name: str, keys: set[str]) -> dict[str, Any]:
    section = data.get(name, {})
    if not isinstance(section, dict):
        raise PolicyError(f"[{name}] must be a table")
    _reject_unknown(section, keys, f"[{name}]")
    return section


def _reject_unknown(data: dict[str, Any], keys: set[str], where: str) -> None:
    unknown = set(data) - keys
    if unknown:
        raise PolicyError(f"Unknown keys in {where}: {', '.join(sorted(unknown))}")


def _bool(data: dict[str, Any], key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise PolicyError(f"{key} must be true or false")
    return value


def _string_list(data: dict[str, Any], key: str, default: list[str]) -> list[str]:
    value = data.get(key, default)
    if not isinstance(value, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in value
    ):
        raise PolicyError(f"{key} must be a list of non-empty strings")
    return list(value)

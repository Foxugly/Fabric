from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fabric_agent.application.policy import (
    LocalPolicy,
    PolicyError,
    PolicyViolation,
    default_policy_path,
)

CLAUDE = "claude_code_local"
POWERSHELL = "windows_powershell"


def _claude(**parameters: Any) -> tuple[str, str, dict[str, Any]]:
    return CLAUDE, f"{CLAUDE}.message.send", {"text": "hi", **parameters}


@pytest.mark.parametrize(
    ("provider", "action", "parameters"),
    [
        _claude(permission_mode="bypassPermissions"),
        _claude(permission_mode="acceptEdits"),
        _claude(permission_prompt=False),
        _claude(allowed_tools=["Bash(*)"]),
        _claude(permission_mode=["bypassPermissions"]),
        (POWERSHELL, f"{POWERSHELL}.command.run", {"command": "whoami"}),
        (POWERSHELL, f"{POWERSHELL}.network.recover", {}),
        ("ghost", "ghost.anything", {}),
    ],
)
def test_defaults_refuse_server_side_relaxations(
    provider: str, action: str, parameters: dict[str, Any]
) -> None:
    with pytest.raises(PolicyViolation):
        LocalPolicy().authorize(provider, action, parameters)


@pytest.mark.parametrize(
    ("provider", "action", "parameters"),
    [
        _claude(),
        _claude(permission_mode="default", disallowed_tools=["Bash"]),
        _claude(permission_mode="plan"),
        (POWERSHELL, f"{POWERSHELL}.command.run", {"operation": "get_location"}),
        (POWERSHELL, "system.info", {}),
        ("echo", "echo.message.send", {"text": "ping"}),
    ],
)
def test_defaults_accept_safe_requests(
    provider: str, action: str, parameters: dict[str, Any]
) -> None:
    LocalPolicy().authorize(provider, action, parameters)


def test_missing_file_means_restrictive_defaults(tmp_path: Path) -> None:
    policy = LocalPolicy.load(tmp_path / "absent.toml")

    assert policy == LocalPolicy()
    assert not policy.allow_raw_powershell
    assert "bypassPermissions" not in policy.allowed_permission_modes


def test_policy_file_can_relax_explicitly(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    path.write_text(
        """
allowed_actions = ["windows_powershell.command.run",
                   "windows_powershell.network.recover"]

[claude_code_local]
allowed_permission_modes = ["default", "acceptEdits"]

[windows_powershell]
allow_raw_commands = true
allow_network_recover = true
network_recover_adapters = ["Wi-Fi"]

[approvals]
require_fingerprint = true
""",
        encoding="utf-8",
    )

    policy = LocalPolicy.load(path)

    policy.authorize(POWERSHELL, "command.run", {"command": "whoami"})
    policy.authorize(POWERSHELL, "network.recover", {"adapter_name": "Wi-Fi"})
    with pytest.raises(PolicyViolation, match="adapter"):
        policy.authorize(POWERSHELL, "network.recover", {"adapter_name": "Ethernet"})
    # Actions left out of allowed_actions are refused.
    with pytest.raises(PolicyViolation, match="not allowed"):
        policy.authorize(*_claude())
    assert policy.require_approval_fingerprint
    assert policy.source == path


@pytest.mark.parametrize(
    "content",
    [
        "allow_everything = true",
        "[windows_powershell]\nallow_raw_commands = 'yes'",
        "allowed_actions = ['windows_powershell.format_disk']",
        "not toml at all [",
    ],
)
def test_a_broken_policy_stops_the_agent(tmp_path: Path, content: str) -> None:
    path = tmp_path / "policy.toml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(PolicyError):
        LocalPolicy.load(path)


def test_working_directories_confine_commands(tmp_path: Path) -> None:
    allowed = tmp_path / "projects"
    (allowed / "app").mkdir(parents=True)
    policy = LocalPolicy(working_directories=(allowed,))

    policy.authorize(*_claude(working_directory=str(allowed / "app")))
    with pytest.raises(PolicyViolation, match="outside"):
        policy.authorize(*_claude(working_directory=str(tmp_path)))
    with pytest.raises(PolicyViolation, match="outside"):
        policy.authorize(
            POWERSHELL,
            "session.create",
            {"working_directory": str(allowed / ".." / "elsewhere")},
        )


def test_policy_path_is_overridable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FABRIC_AGENT_POLICY_FILE", str(tmp_path / "p.toml"))
    assert default_policy_path() == tmp_path / "p.toml"

    monkeypatch.delenv("FABRIC_AGENT_POLICY_FILE")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert default_policy_path() == tmp_path / "fabric-agent" / "policy.toml"

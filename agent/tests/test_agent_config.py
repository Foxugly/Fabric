from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from fabric_agent import token_store
from fabric_agent.application.config import (
    AgentConfig,
    InsecureTransportError,
    ensure_secure_server_url,
)
from fabric_agent.token_store import (
    TokenStoreError,
    load_agent_token,
    store_agent_token,
)


@pytest.mark.parametrize(
    ("url", "allow_insecure"),
    [
        ("wss://fabric.foxugly.com/ws/v1/agent/", False),
        ("ws://127.0.0.1:8000/ws/v1/agent/", True),
        ("ws://localhost:8000/ws/v1/agent/", True),
    ],
)
def test_secure_or_explicit_local_urls_are_accepted(
    url: str, allow_insecure: bool
) -> None:
    ensure_secure_server_url(url, allow_insecure=allow_insecure)


@pytest.mark.parametrize(
    ("url", "allow_insecure"),
    [
        ("ws://127.0.0.1:8000/ws/v1/agent/", False),
        ("ws://fabric.foxugly.com/ws/v1/agent/", False),
        ("ws://fabric.foxugly.com/ws/v1/agent/", True),
        ("ws://127.0.0.1.evil.example/ws/", True),
        ("http://fabric.foxugly.com/ws/v1/agent/", True),
    ],
)
def test_clear_text_urls_are_refused(url: str, allow_insecure: bool) -> None:
    with pytest.raises(InsecureTransportError):
        ensure_secure_server_url(url, allow_insecure=allow_insecure)


def _base_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FABRIC_SERVER_WS_URL", "ws://127.0.0.1:8000/ws/v1/agent/")
    monkeypatch.setenv("FABRIC_AGENT_ID", "agent-1")
    monkeypatch.setenv("FABRIC_AGENT_TOKEN_FILE", str(tmp_path / "agent-token"))
    monkeypatch.delenv("FABRIC_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("FABRIC_AGENT_ALLOW_INSECURE", raising=False)


def test_from_env_refuses_ws_without_the_dev_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("FABRIC_AGENT_TOKEN", "env-token")

    with pytest.raises(InsecureTransportError):
        AgentConfig.from_env()

    monkeypatch.setenv("FABRIC_AGENT_ALLOW_INSECURE", "1")
    config = AgentConfig.from_env()
    assert config.allow_insecure_transport
    assert config.agent_token == "env-token"


def test_token_file_wins_over_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("FABRIC_AGENT_ALLOW_INSECURE", "1")
    monkeypatch.setenv("FABRIC_AGENT_TOKEN", "env-token")
    monkeypatch.setattr(token_store, "_restrict_to_current_user", _chmod_only)
    store_agent_token("  file-token\n")

    assert AgentConfig.from_env().agent_token == "file-token"


def test_no_token_anywhere_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)

    with pytest.raises(TokenStoreError, match="token_store"):
        load_agent_token()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_a_token_file_readable_by_others_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "agent-token"
    path.write_text("secret")
    path.chmod(0o644)

    with pytest.raises(TokenStoreError, match="readable by other users"):
        load_agent_token(path)


def test_storing_on_windows_strips_inherited_acls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(token_store, "_IS_WINDOWS", True)
    monkeypatch.setattr("fabric_agent.token_store.subprocess.run", fake_run)
    monkeypatch.setenv("USERDOMAIN", "PC")
    monkeypatch.setenv("USERNAME", "renaud")

    path = store_agent_token("tok", tmp_path / "agent-token")

    assert calls == [
        ["icacls", str(path), "/inheritance:r", "/grant:r", "PC\\renaud:F"]
    ]


def _chmod_only(path: Path) -> None:
    path.chmod(0o600)

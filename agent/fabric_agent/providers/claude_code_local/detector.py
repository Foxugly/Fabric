from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fabric_agent.providers.claude_code_local.models import (
    ActionRequired,
    SessionStatus,
)


@dataclass(slots=True, frozen=True)
class ClaudeCodeInstallation:
    executable_path: Path | None
    version: str | None

    @property
    def available(self) -> bool:
        return self.executable_path is not None


@dataclass(slots=True, frozen=True)
class TranscriptInventory:
    config_dir: Path
    projects_dir: Path
    transcript_count: int
    latest_transcript_path: Path | None
    skip_prompt_history: bool


class ClaudeCodeInstallationProbe:
    def __init__(self, executable_name: str | None = None) -> None:
        self._executable_name = executable_name or os.environ.get(
            "FABRIC_CLAUDE_CODE_EXECUTABLE", "claude"
        )

    def probe(self) -> ClaudeCodeInstallation:
        executable_path = shutil.which(self._executable_name)
        if executable_path is None:
            return ClaudeCodeInstallation(executable_path=None, version=None)
        resolved_path = Path(executable_path)
        return ClaudeCodeInstallation(
            executable_path=resolved_path,
            version=_read_claude_version(resolved_path),
        )


class ClaudeTranscriptStore:
    def __init__(self, config_dir: Path | None = None) -> None:
        configured_dir = config_dir or _default_config_dir()
        self._config_dir = configured_dir

    def inspect(self) -> TranscriptInventory:
        projects_dir = self._config_dir / "projects"
        transcript_files = sorted(
            projects_dir.rglob("*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ) if projects_dir.exists() else []
        latest_transcript_path = transcript_files[0] if transcript_files else None
        return TranscriptInventory(
            config_dir=self._config_dir,
            projects_dir=projects_dir,
            transcript_count=len(transcript_files),
            latest_transcript_path=latest_transcript_path,
            skip_prompt_history=_is_truthy(
                os.environ.get("CLAUDE_CODE_SKIP_PROMPT_HISTORY", "")
            ),
        )


class ClaudeCodeSessionStatusDetector:
    def __init__(
        self,
        installation_probe: ClaudeCodeInstallationProbe | None = None,
        transcript_store: ClaudeTranscriptStore | None = None,
    ) -> None:
        self._installation_probe = installation_probe or ClaudeCodeInstallationProbe()
        self._transcript_store = transcript_store or ClaudeTranscriptStore()

    def detect(self) -> SessionStatus:
        installation = self._installation_probe.probe()
        inventory = self._transcript_store.inspect()

        if not installation.available:
            return SessionStatus(
                session_detected=False,
                session_ready=False,
                manual_action_required=True,
                transport="unknown",
                action_required=ActionRequired(
                    type="local_session_setup",
                    provider="claude_code_local",
                    message=(
                        "Claude Code CLI was not found on the Windows PC. Install or "
                        "expose the `claude` executable before Fabric can attach."
                    ),
                ),
                details={
                    "config_dir": str(inventory.config_dir),
                    "projects_dir": str(inventory.projects_dir),
                    "transcript_count": inventory.transcript_count,
                },
            )

        if inventory.skip_prompt_history:
            return SessionStatus(
                session_detected=False,
                session_ready=False,
                manual_action_required=True,
                transport="unknown",
                action_required=ActionRequired(
                    type="local_session_setup",
                    provider="claude_code_local",
                    message=(
                        "Claude Code transcript persistence is disabled by "
                        "`CLAUDE_CODE_SKIP_PROMPT_HISTORY`, so Fabric cannot detect a "
                        "resumable local session."
                    ),
                ),
                details={
                    "claude_executable": str(installation.executable_path),
                    "claude_version": installation.version,
                    "config_dir": str(inventory.config_dir),
                },
            )

        if inventory.transcript_count == 0:
            return SessionStatus(
                session_detected=False,
                session_ready=False,
                manual_action_required=True,
                transport="local_session",
                action_required=ActionRequired(
                    type="local_session_setup",
                    provider="claude_code_local",
                    message=(
                        "Claude Code is installed, but no persisted local session was "
                        "detected. Open or resume a local Claude Code session first."
                    ),
                ),
                details={
                    "claude_executable": str(installation.executable_path),
                    "claude_version": installation.version,
                    "config_dir": str(inventory.config_dir),
                    "projects_dir": str(inventory.projects_dir),
                },
            )

        return SessionStatus(
            session_detected=True,
            session_ready=True,
            manual_action_required=False,
            transport="local_session",
            details={
                "claude_executable": str(installation.executable_path),
                "claude_version": installation.version,
                "config_dir": str(inventory.config_dir),
                "projects_dir": str(inventory.projects_dir),
                "transcript_count": inventory.transcript_count,
                "latest_transcript_path": (
                    str(inventory.latest_transcript_path)
                    if inventory.latest_transcript_path is not None
                    else None
                ),
            },
        )


def _default_config_dir() -> Path:
    configured_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if configured_dir:
        return Path(configured_dir).expanduser()
    return Path.home() / ".claude"


def _read_claude_version(executable_path: Path) -> str | None:
    try:
        completed = subprocess.run(
            [str(executable_path), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    version = completed.stdout.strip() or completed.stderr.strip()
    return version or None


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


class ClaudeCodeInstallationProbeLike(Protocol):
    def probe(self) -> ClaudeCodeInstallation:
        ...

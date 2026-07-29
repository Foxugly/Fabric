from __future__ import annotations

from pathlib import Path

import pytest

from fabric_agent.providers.claude_code_local.detector import (
    ClaudeCodeInstallation,
    ClaudeCodeInstallationProbe,
    ClaudeCodeSessionStatusDetector,
    ClaudeTranscriptStore,
)
from fabric_agent.providers.claude_code_local.provider import ClaudeCodeLocalProvider


class StubInstallationProbe(ClaudeCodeInstallationProbe):
    def __init__(self, installation: ClaudeCodeInstallation) -> None:
        self._installation = installation

    def probe(self) -> ClaudeCodeInstallation:
        return self._installation


def test_transcript_store_finds_latest_transcript(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects" / "repo"
    projects_dir.mkdir(parents=True)
    older = projects_dir / "older.jsonl"
    latest = projects_dir / "latest.jsonl"
    older.write_text("{}", encoding="utf-8")
    latest.write_text("{}", encoding="utf-8")

    store = ClaudeTranscriptStore(config_dir=tmp_path)

    inventory = store.inspect()

    assert inventory.transcript_count == 2
    assert inventory.latest_transcript_path == latest


def test_session_status_reports_missing_claude(tmp_path: Path) -> None:
    detector = ClaudeCodeSessionStatusDetector(
        installation_probe=StubInstallationProbe(
            ClaudeCodeInstallation(executable_path=None, version=None)
        ),
        transcript_store=ClaudeTranscriptStore(config_dir=tmp_path),
    )

    status = detector.detect().to_dict()

    assert status["session_detected"] is False
    assert status["manual_action_required"] is True
    assert "Install or expose" in status["action_required"]["message"]


def test_session_status_reports_no_local_session(tmp_path: Path) -> None:
    detector = ClaudeCodeSessionStatusDetector(
        installation_probe=StubInstallationProbe(
            ClaudeCodeInstallation(
                executable_path=Path("C:/Tools/claude.exe"),
                version="claude 2.1.212",
            )
        ),
        transcript_store=ClaudeTranscriptStore(config_dir=tmp_path),
    )

    status = detector.detect().to_dict()

    assert status["session_detected"] is False
    assert status["transport"] == "local_session"
    assert status["details"]["claude_version"] == "claude 2.1.212"


def test_session_status_reports_ready_when_transcript_exists(tmp_path: Path) -> None:
    transcript_dir = tmp_path / "projects" / "repo"
    transcript_dir.mkdir(parents=True)
    transcript_path = transcript_dir / "session-1.jsonl"
    transcript_path.write_text("{}", encoding="utf-8")

    detector = ClaudeCodeSessionStatusDetector(
        installation_probe=StubInstallationProbe(
            ClaudeCodeInstallation(
                executable_path=Path("C:/Tools/claude.exe"),
                version="claude 2.1.212",
            )
        ),
        transcript_store=ClaudeTranscriptStore(config_dir=tmp_path),
    )

    status = detector.detect().to_dict()

    assert status["session_detected"] is True
    assert status["session_ready"] is True
    assert status["manual_action_required"] is False
    assert status["details"]["latest_transcript_path"] == str(transcript_path)


@pytest.mark.asyncio
async def test_provider_uses_detector_status(tmp_path: Path) -> None:
    transcript_dir = tmp_path / "projects" / "repo"
    transcript_dir.mkdir(parents=True)
    transcript_path = transcript_dir / "session-1.jsonl"
    transcript_path.write_text("{}", encoding="utf-8")

    provider = ClaudeCodeLocalProvider(
        status_detector=ClaudeCodeSessionStatusDetector(
            installation_probe=StubInstallationProbe(
                ClaudeCodeInstallation(
                    executable_path=Path("C:/Tools/claude.exe"),
                    version="claude 2.1.212",
                )
            ),
            transcript_store=ClaudeTranscriptStore(config_dir=tmp_path),
        )
    )

    status = await provider.get_status()

    assert status["provider"] == "claude_code_local"
    assert status["details"]["latest_transcript_path"] == str(transcript_path)

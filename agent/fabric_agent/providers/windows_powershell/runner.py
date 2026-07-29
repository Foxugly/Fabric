from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class PowerShellExecutionError(RuntimeError):
    """Raised when a PowerShell action cannot be executed safely."""


@dataclass(slots=True, frozen=True)
class PowerShellProbeResult:
    executable_path: Path | None

    @property
    def available(self) -> bool:
        return self.executable_path is not None


class PowerShellProbe:
    def __init__(
        self,
        executable_names: tuple[str, ...] = ("powershell", "pwsh"),
    ) -> None:
        self._executable_names = executable_names

    def probe(self) -> PowerShellProbeResult:
        for executable_name in self._executable_names:
            executable_path = shutil.which(executable_name)
            if executable_path:
                return PowerShellProbeResult(Path(executable_path))
        return PowerShellProbeResult(None)


class PowerShellRunner:
    def __init__(self, executable_path: Path) -> None:
        self._executable_path = executable_path

    async def run_json(
        self,
        script: str,
        *,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            str(self._executable_path),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
        if process.returncode != 0:
            raise PowerShellExecutionError(
                f"PowerShell action failed with exit code {process.returncode}: "
                f"{stderr.decode('utf-8', errors='replace').strip()}"
            )
        return _parse_json_object(stdout.decode("utf-8", errors="replace"))


def _parse_json_object(raw_output: str) -> dict[str, Any]:
    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise PowerShellExecutionError(
            "PowerShell action did not return valid JSON"
        ) from exc
    if not isinstance(data, dict):
        raise PowerShellExecutionError("PowerShell action result must be a JSON object")
    return data


class PowerShellProbeLike(Protocol):
    def probe(self) -> PowerShellProbeResult:
        ...

"""Where the agent token lives on the PC.

The token is a long-lived credential that drives this machine, so it should not
sit in a `.env` or a launcher script. It is kept in a file readable by the
logged-in user only (`%APPDATA%\\fabric-agent\\agent-token` by default,
overridable with `FABRIC_AGENT_TOKEN_FILE`). `FABRIC_AGENT_TOKEN` remains a
fallback for development.

Store it once with::

    python -m fabric_agent.token_store

which reads the token from stdin (no echo, never on the command line), writes
the file and, on Windows, strips inherited ACLs so only the current user can
read it.
"""

from __future__ import annotations

import getpass
import os
import stat
import subprocess
import sys
from pathlib import Path

TOKEN_FILE_ENV = "FABRIC_AGENT_TOKEN_FILE"
TOKEN_ENV = "FABRIC_AGENT_TOKEN"
TOKEN_FILE_NAME = "agent-token"
_IS_WINDOWS = os.name == "nt"


class TokenStoreError(RuntimeError):
    pass


def default_token_path() -> Path:
    configured = os.environ.get(TOKEN_FILE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "fabric-agent" / TOKEN_FILE_NAME
    return Path.home() / ".config" / "fabric-agent" / TOKEN_FILE_NAME


def load_agent_token(path: Path | None = None) -> str:
    """Token file first, then the `FABRIC_AGENT_TOKEN` environment variable."""
    token_path = path or default_token_path()
    if token_path.is_file():
        _check_private(token_path)
        token = token_path.read_text(encoding="utf-8").strip()
        if not token:
            raise TokenStoreError(f"Agent token file {token_path} is empty")
        return token
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        return token
    raise TokenStoreError(
        f"No agent token: create {token_path} with "
        "`python -m fabric_agent.token_store` or set FABRIC_AGENT_TOKEN"
    )


def store_agent_token(token: str, path: Path | None = None) -> Path:
    token = token.strip()
    if not token:
        raise TokenStoreError("Refusing to store an empty token")
    token_path = path or default_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    _restrict_to_current_user(token_path)
    return token_path


def _restrict_to_current_user(path: Path) -> None:
    if not _IS_WINDOWS:
        path.chmod(0o600)
        return
    user = os.environ.get("USERNAME") or getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    principal = f"{domain}\\{user}" if domain else user
    # Drop inherited ACEs, then grant the current user alone.
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:F"],
        check=True,
        capture_output=True,
    )


def _check_private(path: Path) -> None:
    """POSIX only: Windows ACLs are set by `store_agent_token` (icacls)."""
    if _IS_WINDOWS:
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise TokenStoreError(
            f"Agent token file {path} is readable by other users "
            f"(mode {oct(mode)}); run `chmod 600 {path}`"
        )


def main() -> None:
    token = getpass.getpass("Fabric agent token: ")
    try:
        path = store_agent_token(token)
    except (TokenStoreError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Could not store the token: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Token stored in {path} (readable by the current user only).")


if __name__ == "__main__":
    main()

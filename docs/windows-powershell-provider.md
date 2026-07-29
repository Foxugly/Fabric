# Windows PowerShell Provider

Fabric exposes a controlled PowerShell provider on the Windows agent:

```text
windows_powershell
```

This provider supports:

- one-shot safe diagnostic actions
- persistent PowerShell sessions with state
- **arbitrary PowerShell commands inside a persistent session**
- optional local network recovery actions

> **This provider IS an arbitrary remote shell.** `windows_powershell.command.run`
> accepts a free-form `command` string and evaluates it through
> `[scriptblock]::Create()` in the persistent session. Anyone who can create a
> command for an agent can run any code as the agent's Windows user. See
> [Security model](#security-model).

## One-shot actions

The following actions can be sent directly:

- `windows_powershell.system.info`
- `windows_powershell.process.list`
- `windows_powershell.claude.version`
- `windows_powershell.network.recover`

Example:

```json
{
  "provider": "windows_powershell",
  "action": "windows_powershell.system.info",
  "payload": {}
}
```

## Persistent session lifecycle

Persistent PowerShell sessions keep the same PowerShell process alive across multiple commands.

Supported actions:

- `windows_powershell.session.create`
- `windows_powershell.session.status`
- `windows_powershell.session.close`
- `windows_powershell.command.run`

### Create

```json
{
  "provider": "windows_powershell",
  "action": "windows_powershell.session.create",
  "payload": {
    "working_directory": "C:\\Users\\rvilain\\PycharmProjects\\Fabric"
  }
}
```

Response shape:

```json
{
  "provider": "windows_powershell",
  "session": {
    "session_id": "uuid",
    "working_directory": "C:\\Users\\rvilain\\PycharmProjects\\Fabric",
    "created_at": "ISO-8601",
    "last_used_at": "ISO-8601",
    "available": true,
    "busy": false
  }
}
```

### Status

```json
{
  "provider": "windows_powershell",
  "action": "windows_powershell.session.status",
  "payload": {
    "session_id": "uuid"
  }
}
```

### Close

```json
{
  "provider": "windows_powershell",
  "action": "windows_powershell.session.close",
  "payload": {
    "session_id": "uuid"
  }
}
```

## Raw commands

`windows_powershell.command.run` accepts a free-form `command` and streams its
output back as `terminal.stdout` / `terminal.stderr` progress events. This is what
the Fabric terminal UI uses for every line that is not a local or `claude` command.

```json
{
  "provider": "windows_powershell",
  "action": "windows_powershell.command.run",
  "payload": {
    "session_id": "uuid",
    "command": "git status",
    "timeout_seconds": 60
  }
}
```

The command runs with the privileges of the account running the agent. There is
no allow-list, no sandbox and no path restriction.

## Persistent session operations

When `command` is absent, `windows_powershell.command.run` falls back to a
structured `operation`. These are the constrained, parameter-validated helpers:

- `get_location`
- `set_location`
- `test_path`
- `get_child_items`
- `claude_version`
- `get_service_status`
- `get_runtime_status`
- `get_network_status`
- `get_python_process_status`
- `get_claude_process_status`
- `get_event_log`
- `get_file_summary`

Example:

```json
{
  "provider": "windows_powershell",
  "action": "windows_powershell.command.run",
  "payload": {
    "session_id": "uuid",
    "operation": "get_file_summary",
    "path": "C:\\Users\\rvilain\\PycharmProjects\\Fabric"
  }
}
```

## Connectivity watchdog

The Windows agent includes a connectivity watchdog. It periodically checks:

- WebSocket liveness
- WebSocket ping/pong health
- TCP reachability to the Fabric server

If repeated checks fail, the agent closes the socket and reconnects cleanly.

Optional network self-heal can be enabled through environment variables:

- `FABRIC_CONNECTIVITY_CHECK_SECONDS`
- `FABRIC_CONNECTIVITY_FAILURE_THRESHOLD`
- `FABRIC_NETWORK_RECOVERY_ENABLED`
- `FABRIC_NETWORK_RECOVERY_COOLDOWN_SECONDS`
- `FABRIC_NETWORK_RECOVERY_ADAPTER_NAME`

By default, network recovery is disabled.

## Security model

Only the **structured `operation` path** is constrained:

- parameters are validated and quoted with `_ps_quote`
- `name_like`, `log_name`, `max_items` are pattern- or range-checked
- timeouts are enforced (1 to 120 seconds)
- session access is keyed by `session_id`

The **raw `command` path is not constrained at all**. Treat an agent that
registers this provider as equivalent to an open remote shell on that Windows
machine, and secure it accordingly:

- the Fabric API is the only authorisation boundary — anyone who can
  authenticate and reach `POST /api/v1/commands/` owns the machine;
- Fabric currently has **no per-agent ownership**: every authenticated user can
  drive every registered agent. Do not expose a multi-user Fabric instance to
  the Internet until that is fixed (see `docs/audit-2026-07-29.md`, P0-2);
- run the agent as an unprivileged Windows account, never as an administrator;
- the agent's development token is a bearer credential; rotate it with
  `POST /api/v1/agents/<id>/development-token` and revoke with `.../revoke`.

# Windows PowerShell Provider

Fabric exposes a controlled PowerShell provider on the Windows agent:

```text
windows_powershell
```

This provider supports:

- one-shot safe diagnostic actions
- persistent PowerShell sessions with state
- optional local network recovery actions

It does **not** expose an arbitrary remote shell.

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

## Persistent session operations

`windows_powershell.command.run` currently supports these `operation` values:

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

The provider is intentionally constrained:

- no arbitrary PowerShell script payload
- no arbitrary shell command payload
- only explicit whitelisted actions and operations
- timeouts enforced on operations
- session access is keyed by `session_id`

If a true arbitrary persistent console is required later, that should be treated as a separate high-risk mode.

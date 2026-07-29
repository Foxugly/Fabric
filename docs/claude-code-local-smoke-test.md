# Claude Code Local Smoke Test

This smoke test validates the real `claude_code_local` path against a user-owned Windows machine with Claude Code installed and logged in.

## Preconditions

- Fabric backend is running
- Fabric agent is running
- the `claude` CLI is installed on the Windows machine
- the user is already authenticated in Claude Code
- a local Claude Code session has been created at least once in the target working directory

## 1. Confirm the CLI is visible

On the Windows machine:

```powershell
claude --version
```

Expected result:

- the command succeeds
- a version string is printed

## 2. Confirm a resumable local session exists

In the target repository:

```powershell
claude --continue
```

Exit after the session opens. This ensures local transcript persistence exists for the working directory.

## 3. Check provider capabilities through Fabric

Confirm the agent exposes `claude_code_local` capabilities through the backend.

```bash
curl http://127.0.0.1:8000/api/v1/agents/
```

Expected result:

- the agent is `online`
- `capabilities` contains:
  - `session.status`
  - `session.attach`
  - `message.send`
  - `message.stream`
  - `message.cancel`

## 4. Send `claude_code_local.session.status`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/commands/ ^
  -H "Content-Type: application/json" ^
  -d "{\"agent_id\":\"<agent_id>\",\"provider\":\"claude_code_local\",\"action\":\"claude_code_local.session.status\",\"payload\":{}}"
```

Then poll:

```bash
curl http://127.0.0.1:8000/api/v1/commands/<command_id>/
```

Expected result:

- status transitions to `succeeded`
- `result.provider == "claude_code_local"`
- `result.transport` is typically `local_session`

## 5. Send `claude_code_local.message.send`

Run in the repository that owns the session:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/commands/ ^
  -H "Content-Type: application/json" ^
  -d "{\"agent_id\":\"<agent_id>\",\"provider\":\"claude_code_local\",\"action\":\"claude_code_local.message.send\",\"payload\":{\"text\":\"Reply with exactly: FABRIC_SMOKE_OK\",\"working_directory\":\"C:\\\\path\\\\to\\\\repo\",\"timeout_seconds\":180}}"
```

Then poll:

```bash
curl http://127.0.0.1:8000/api/v1/commands/<command_id>/
```

Expected result:

- status transitions to `succeeded`
- `events` may contain message deltas
- `result.text` contains `FABRIC_SMOKE_OK`
- `result.session_id` is populated when Claude Code returns one

## Notes

- `claude_code_local.message.send` uses the Claude Code CLI, not transcript parsing.
- transcript files are used only for conservative session detection.
- direct transcript parsing should remain out of the execution path because the transcript format is internal and can change across Claude Code releases.

# Agent Architecture

## Registered providers

The agent runtime registers three providers ([registry.py](../agent/fabric_agent/application/registry.py)):

| Provider | Role |
|---|---|
| `claude_code_local` | drives the user's local Claude Code CLI |
| `windows_powershell` | persistent PowerShell sessions and raw commands |
| `echo` | transport test harness, no side effects |

## Action namespace

Actions are **fully qualified on the wire**: the API, the database
(`ALLOWED_ACTIONS`), the protocol envelope and the advertised
`agent.capabilities` all use `<provider>.<action>`, e.g.
`claude_code_local.message.send`.

The `CommandDispatcher` strips the provider prefix before calling the provider,
so a provider only ever matches its own unqualified action (`message.send`).
Adding a provider means implementing unqualified actions and advertising
qualified capabilities — do not mix the two conventions inside a provider.

```text
API "claude_code_local.message.send"
  -> protocol envelope (qualified)
  -> CommandDispatcher.local_action() strips the prefix
  -> ClaudeCodeLocalProvider.execute("message.send", payload)
```

## Execution contract

For each `command.request` the transport client:

1. answers `command.accepted`, then `command.started`;
2. consumes `provider.stream(action, payload)` and forwards every yielded event
   as `command.progress`;
3. calls `provider.execute(action, payload)` for the final result and sends
   `command.completed`, or `command.failed` on error.

Providers that stream therefore cache their final result during the stream so
that `execute()` does not run the work twice. The stream is wrapped in
`contextlib.aclosing` at both the dispatcher and client level: a provider may
hold a lock across `yield` and must be finalised if the consumer stops early.

Two payload keys are injected by the client and are reserved:

- `_fabric_command_id` — identifies the command, used for cancellation
- `_fabric_timeout_seconds` — the `timeout_seconds` of the `Command` row

## `claude_code_local`

The provider shells out to the `claude` CLI in print mode; it does not parse
transcripts. Transcript files are only read for conservative session detection
in `session.status`.

| Concern | Implementation |
|---|---|
| detection | `detector.py` — `claude` on PATH, config dir, `projects/*.jsonl` |
| one-shot | `claude -p --output-format json` |
| streaming | `claude -p --output-format stream-json --verbose --include-partial-messages` |
| continuity | `--resume <session_id>`; the returned `session_id` must be re-used |
| options | `--permission-mode`, `--model`, `--allowed-tools`, `--disallowed-tools` |
| approvals | `--permission-prompt-tool` bridged to the web UI, see [permission-bridge.md](permission-bridge.md) |
| cancellation | the running child process is killed by `_fabric_command_id` |

Progress events carry `message.delta` (text) and `message.tool_use` (a one-line
summary of each tool call) so a terminal can show activity, not just the answer.

See [claude-in-the-terminal.md](claude-in-the-terminal.md) and
[claude-code-local-smoke-test.md](claude-code-local-smoke-test.md).

## Protocol messages beyond commands

| Message | Direction | Role |
|---|---|---|
| `session.action_required` | agent → Fabric | a tool call needs a human decision |
| `session.action_response` | Fabric → agent | the decision, which unblocks the turn |
| `command.permission_request` | Fabric → browser | surfaces the question in the UI |

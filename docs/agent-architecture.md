# Agent Architecture

## Current state

The current agent runtime supports one executable test provider:

- `echo`

This provider exists to validate the transport chain:

```text
Fabric API -> WebSocket agent transport -> provider execution -> progress -> result
```

## Planned provider

The target provider for the next real integration is:

```text
claude_code_local
```

It is intentionally scaffolded but not registered as an active provider yet.

## Why the provider is not registered yet

The current code keeps `echo` as the only active provider because:

- the transport path is already validated by tests;
- `claude_code_local` still needs a real session adapter;
- exposing it before implementation would create a misleading capability surface.

## Planned internal structure

The package lives at:

- [agent/fabric_agent/providers/claude_code_local](/C:/Users/rvilain/PycharmProjects/Fabric/agent/fabric_agent/providers/claude_code_local)

The intended responsibilities are:

- session detection
- session attach
- command execution
- progress extraction
- cancellation
- manual action reporting

## Current contracts

The scaffold currently freezes three data contracts:

- `ActionRequired`
- `SessionStatus`
- `ClaudeCodeLocalCapabilities`

The first real `session.status` implementation is now based on conservative local signals:

- presence of the `claude` executable
- configured Claude Code config directory
- persisted local transcript files under `projects/`
- the `CLAUDE_CODE_SKIP_PROMPT_HISTORY` flag when history is disabled

These contracts are defined in:

- [agent/fabric_agent/providers/claude_code_local/models.py](/C:/Users/rvilain/PycharmProjects/Fabric/agent/fabric_agent/providers/claude_code_local/models.py)

The provider stub is defined in:

- [agent/fabric_agent/providers/claude_code_local/provider.py](/C:/Users/rvilain/PycharmProjects/Fabric/agent/fabric_agent/providers/claude_code_local/provider.py)

The detection logic is defined in:

- [agent/fabric_agent/providers/claude_code_local/detector.py](/C:/Users/rvilain/PycharmProjects/Fabric/agent/fabric_agent/providers/claude_code_local/detector.py)

## Next implementation step

The next meaningful coding step is to add a real session adapter behind `claude_code_local`, then register the provider only once:

1. `session.status` returns real detection data
2. `message.send` can attach to a local session
3. `message.stream` emits real deltas
4. `message.cancel` interrupts an in-flight turn

/**
 * Defaults of a Claude Code turn sent from the terminal.
 *
 * The timeout bounds the whole turn on the PC (Claude's work plus any time spent
 * answering a permission request). It travels in the payload, so changing it
 * here applies without updating the agent. The agent caps it at 3600 s.
 */
export const CLAUDE_TIMEOUT_SECONDS = 1200;

export const CLAUDE_PERMISSION_MODES = new Set(['acceptEdits', 'auto', 'bypassPermissions', 'manual', 'plan', 'default']);

/**
 * `bypassPermissions`: Claude runs every tool without asking. The PC still has the
 * last word: its `policy.toml` must list the mode in `allowed_permission_modes`,
 * otherwise the turn is refused (see `explainClaudeFailure`). `claude:mode default`
 * routes every approval through Fabric's permission bridge instead.
 */
export const DEFAULT_CLAUDE_PERMISSION_MODE = 'bypassPermissions';

const REFUSED_PERMISSION_MODE = /permission_mode '([^']+)' is not allowed on this PC/;

/**
 * Turn an agent error into something actionable. A permission mode refused by the
 * PC's local policy gets the two ways out: allow it on the PC, or pick another mode.
 */
export function explainClaudeFailure(error: string): string {
  const refused = REFUSED_PERMISSION_MODE.exec(error);
  if (refused === null) {
    return error;
  }
  const mode = refused[1];
  return [
    error,
    `The agent's policy.toml on the PC does not allow '${mode}'. Either add it to`,
    `[claude_code_local] allowed_permission_modes in %APPDATA%\\fabric-agent\\policy.toml`,
    'and restart the agent, or switch mode here: claude:mode default (asks before each tool).',
  ].join('\n');
}

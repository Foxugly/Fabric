import {
  CLAUDE_PERMISSION_MODES,
  CLAUDE_TIMEOUT_SECONDS,
  DEFAULT_CLAUDE_PERMISSION_MODE,
  explainClaudeFailure,
} from './claude-settings';

describe('claude settings', () => {
  it('bypasses permissions by default, with a mode the terminal accepts', () => {
    expect(DEFAULT_CLAUDE_PERMISSION_MODE).toBe('bypassPermissions');
    expect(CLAUDE_PERMISSION_MODES.has(DEFAULT_CLAUDE_PERMISSION_MODE)).toBe(true);
  });

  it('gives a turn 20 minutes, within the agent cap', () => {
    expect(CLAUDE_TIMEOUT_SECONDS).toBe(1200);
    expect(CLAUDE_TIMEOUT_SECONDS).toBeLessThanOrEqual(3600);
  });

  it('explains a permission mode refused by the PC policy', () => {
    const error =
      "Refused by the agent's local policy: permission_mode 'bypassPermissions' " +
      'is not allowed on this PC (allowed: default, plan)';
    const explained = explainClaudeFailure(error);
    expect(explained.startsWith(error)).toBe(true);
    expect(explained).toContain("does not allow 'bypassPermissions'");
    expect(explained).toContain('allowed_permission_modes');
    expect(explained).toContain('%APPDATA%\\fabric-agent\\policy.toml');
    expect(explained).toContain('claude:mode default');
  });

  it('leaves every other error untouched', () => {
    expect(explainClaudeFailure('Claude Code timed out')).toBe('Claude Code timed out');
    expect(explainClaudeFailure('')).toBe('');
  });
});

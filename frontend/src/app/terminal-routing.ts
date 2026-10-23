/** Terminal lines that stay local even in Claude mode (they drive the terminal itself). */
export const LOCAL_TERMINAL_COMMANDS = new Set(['help', 'clear', 'cls', 'open', 'close', 'exit', 'cancel']);

export interface RoutedTerminalLine {
  /** Line to dispatch, possibly rewritten (`claude ` prefix added, `!` stripped). */
  line: string;
  /** True when the line must go straight to PowerShell, skipping every other handler. */
  powerShell: boolean;
}

/**
 * Claude mode (`claude:on`) sends every line to Claude without the `claude` prefix.
 * Still handled as usual: `claude…` lines and the local terminal commands.
 * `!command` runs one PowerShell command. Outside Claude mode nothing changes.
 */
export function routeTerminalLine(line: string, claudeMode: boolean): RoutedTerminalLine {
  if (!claudeMode) {
    return { line, powerShell: false };
  }
  if (line.startsWith('!')) {
    return { line: line.slice(1).trim(), powerShell: true };
  }
  if (/^claude(:|\s|$)/.test(line) || LOCAL_TERMINAL_COMMANDS.has(line.toLowerCase())) {
    return { line, powerShell: false };
  }
  return { line: `claude ${line}`, powerShell: false };
}

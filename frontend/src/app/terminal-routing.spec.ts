import { routeTerminalLine } from './terminal-routing';

describe('terminal routing', () => {
  it('leaves every line untouched outside Claude mode', () => {
    expect(routeTerminalLine('tu es là ?', false)).toEqual({ line: 'tu es là ?', powerShell: false });
    expect(routeTerminalLine('!dir', false)).toEqual({ line: '!dir', powerShell: false });
  });

  it('sends plain lines to Claude in Claude mode', () => {
    expect(routeTerminalLine('tu es là ?', true)).toEqual({ line: 'claude tu es là ?', powerShell: false });
  });

  it('keeps claude subcommands and local commands as they are', () => {
    for (const line of ['claude:off', 'claude:new', 'claude hello', 'claude', 'help', 'CLEAR', 'cancel']) {
      expect(routeTerminalLine(line, true)).toEqual({ line, powerShell: false });
    }
  });

  it('does not mistake a word starting with claude for the command', () => {
    expect(routeTerminalLine('claudette', true)).toEqual({ line: 'claude claudette', powerShell: false });
  });

  it('runs !command in PowerShell', () => {
    expect(routeTerminalLine('! Get-Date', true)).toEqual({ line: 'Get-Date', powerShell: true });
  });
});

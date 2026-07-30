import { NgClass, NgFor, NgIf } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  HostListener,
  OnDestroy,
  effect,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom, Subscription } from 'rxjs';
import { TerminalModule, TerminalService } from 'primeng/terminal';

import { Agent, AuthSession, Command, CommandStatus, FabricEvent } from './models';

import { AgentService } from './services/agent.service';
import { AuthService } from './services/auth.service';
import { CommandService } from './services/command.service';
import { EventStreamService } from './services/event-stream.service';
import { RuntimeErrorService } from './services/runtime-error.service';

const FINAL_COMMAND_STATUSES = new Set<CommandStatus>([
  'succeeded',
  'failed',
  'cancelled',
  'timed_out',
]);
const TERMINAL_HISTORY_STORAGE_KEY = 'fabric.terminal.history';
const TERMINAL_STATE_STORAGE_KEY = 'fabric.terminal.state';
const TERMINAL_HISTORY_LIMIT = 200;
const TERMINAL_OUTPUT_MAX_CHARS = 120_000;
const TERMINAL_OUTPUT_TRIM_MARKER = '[output trimmed]\n';

const CLAUDE_PROVIDER = 'claude_code_local';
const POWERSHELL_PROVIDER = 'windows_powershell';
const CLAUDE_TIMEOUT_SECONDS = 600;
const CLAUDE_PERMISSION_MODES = new Set(['acceptEdits', 'auto', 'bypassPermissions', 'manual', 'plan', 'default']);
// `default` routes every approval through Fabric's permission bridge instead of
// granting them up front. Switch to `acceptEdits` to stop being asked.
const DEFAULT_CLAUDE_PERMISSION_MODE = 'default';

interface PendingPermission {
  requestId: string;
  toolName: string;
  toolInput: Record<string, unknown>;
}

@Component({
  selector: 'fabric-root',
  standalone: true,
  imports: [FormsModule, NgClass, NgFor, NgIf, TerminalModule],
  providers: [TerminalService],
  template: `
    <div class="shell">
      <ng-container *ngIf="session(); else loginView">
        <header class="topbar">
          <div class="brand">
            <h1>Fabric</h1>
            <div class="status-row">
              <span class="status-dot" [ngClass]="selectedAgentStatus()"></span>
              <span class="status-text">{{ selectedAgentStatus() }}</span>
              <span class="separator" *ngIf="powerShellSessionId()">·</span>
              <span class="status-text" *ngIf="powerShellSessionId()">session open</span>
              <span class="separator" *ngIf="activeCommandId()">·</span>
              <span class="status-text" *ngIf="activeCommandId()">running</span>
            </div>
          </div>

          <div class="controls">
            <select
              *ngIf="agents().length > 1"
              [ngModel]="selectedAgentId()"
              (ngModelChange)="selectAgentById($event)"
              [disabled]="controlsLocked()"
            >
              <option *ngFor="let agent of agents()" [ngValue]="agent.id">{{ agent.name }}</option>
            </select>
            <input
              [ngModel]="workingDirectory()"
              (ngModelChange)="workingDirectory.set($event)"
              placeholder="Working directory (e.g. C:\\Projects\\my-repo)"
              [disabled]="controlsLocked()"
            />
            <button
              type="button"
              [disabled]="controlsLocked() || selectedAgent() === null || selectedAgentStatus() === 'offline'"
              (click)="openSession()"
            >
              Open
            </button>
            <button
              type="button"
              class="secondary-button"
              [disabled]="controlsLocked() || powerShellSessionId() === null"
              (click)="closeSession()"
            >
              Close
            </button>
            <button
              type="button"
              class="secondary-button"
              [disabled]="activeCommandId() === null"
              (click)="cancelActiveCommand()"
            >
              Cancel
            </button>
            <button
              type="button"
              class="secondary-button"
              [disabled]="!hasTerminalOutput()"
              (click)="copyTerminalOutput()"
            >
              Copy output
            </button>
            <button type="button" class="secondary-button" (click)="logout()">Sign out</button>
          </div>
        </header>

        <!--
          A pending approval blocks a turn on the machine, so it gets a card of
          its own rather than a line in the scrollback: on a phone, typing
          "allow" against autocorrect is hostile, and this is the gesture the
          operator makes most often.
        -->
        <section class="permission" *ngIf="pendingPermission() as pending">
          <div class="permission__what">
            <span class="permission__tool">{{ pending.toolName }}</span>
            <span class="permission__hint" *ngIf="permissionHint() as hint">{{ hint }}</span>
          </div>
          <div class="permission__actions">
            <button
              type="button"
              class="permission__allow"
              [disabled]="decidingPermission()"
              (click)="decidePermission(true)"
            >
              Autoriser
            </button>
            <button
              type="button"
              class="permission__deny"
              [disabled]="decidingPermission()"
              (click)="decidePermission(false)"
            >
              Refuser
            </button>
          </div>
        </section>

        <main class="terminal-shell">
          <p-terminal
            *ngIf="terminalMounted()"
            [welcomeMessage]="welcomeMessage"
            [prompt]="terminalPrompt()"
            styleClass="fabric-terminal"
            [ngClass]="{ 'foreground-running': shouldHidePromptWhileRunning() }"
            [style]="{ height: 'calc(100vh - 116px)' }"
          />
        </main>

        <section class="runtime-error" *ngIf="displayError() as errorMessage">
          <pre>{{ errorMessage }}</pre>
        </section>
      </ng-container>

      <ng-template #loginView>
        <div class="login-shell">
          <section class="login-panel">
            <form (ngSubmit)="submitLogin()">
              <h2>Sign in</h2>
              <input
                name="username"
                [ngModel]="loginUsername()"
                (ngModelChange)="loginUsername.set($event)"
                autocomplete="username"
                placeholder="Username"
              />
              <input
                type="password"
                name="password"
                [ngModel]="loginPassword()"
                (ngModelChange)="loginPassword.set($event)"
                autocomplete="current-password"
                placeholder="Password"
              />
              <button type="submit">Sign in</button>
              <div class="login-error" *ngIf="loginError()">{{ loginError() }}</div>
            </form>
          </section>
        </div>
      </ng-template>
    </div>
  `,
  styles: [`
    .shell {
      min-height: 100vh;
      background: #0b1015;
      color: #d9e2ec;
    }

    .topbar {
      height: 76px;
      padding: 14px 18px;
      border-bottom: 1px solid #1f2a37;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      background: #0f1419;
    }

    .brand,
    .controls,
    .status-row,
    .login-panel form {
      display: flex;
      gap: 12px;
      align-items: center;
    }

    .brand {
      flex-direction: column;
      align-items: flex-start;
      gap: 6px;
    }

    h1,
    h2,
    pre {
      margin: 0;
    }

    h1 {
      font-size: 18px;
      font-weight: 600;
    }

    .status-row {
      color: #94a3b8;
      font-size: 13px;
    }

    .status-dot {
      width: 9px;
      height: 9px;
      border-radius: 999px;
      background: #64748b;
      display: inline-block;
    }

    .status-dot.online,
    .status-dot.busy {
      background: #22c55e;
    }

    .status-dot.connecting {
      background: #f59e0b;
    }

    .status-dot.error,
    .status-dot.offline,
    .status-dot.disabled {
      background: #ef4444;
    }

    .separator {
      color: #475569;
    }

    .controls {
      flex-wrap: wrap;
      justify-content: flex-end;
    }

    select,
    input,
    button {
      height: 40px;
      border-radius: 8px;
      border: 1px solid #25384a;
      background: #111827;
      color: inherit;
      padding: 0 12px;
      font: inherit;
    }

    input {
      width: min(420px, 48vw);
    }

    button {
      cursor: pointer;
      background: #1d4ed8;
      border-color: #3267c3;
      color: #f8fbff;
    }

    button:disabled {
      opacity: 0.55;
      cursor: default;
    }

    .secondary-button {
      background: #1f2937;
      border-color: #374151;
    }

    .terminal-shell {
      padding: 18px;
    }

    :host ::ng-deep .fabric-terminal {
      border: 1px solid #1f2a37;
      background: #05080c;
      border-radius: 10px;
      overflow: hidden;
    }

    :host ::ng-deep .fabric-terminal .p-terminal-content {
      background: #05080c;
      color: #d4f4dd;
      font-family: Consolas, "Courier New", monospace;
      font-size: 13px;
      line-height: 1.5;
      padding: 16px;
      overflow: auto;
      user-select: text;
    }

    :host ::ng-deep .fabric-terminal .p-terminal-prompt {
      color: #60a5fa;
    }

    :host ::ng-deep .fabric-terminal.foreground-running .p-terminal-prompt {
      display: none;
    }

    :host ::ng-deep .fabric-terminal .p-terminal-command {
      color: #f8fafc;
    }

    :host ::ng-deep .fabric-terminal .p-terminal-command-response {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      user-select: text;
    }

    :host ::ng-deep .fabric-terminal .p-terminal-command-response::selection,
    :host ::ng-deep .fabric-terminal .p-terminal-command-value::selection,
    :host ::ng-deep .fabric-terminal .p-terminal-welcome-message::selection {
      background: rgba(96, 165, 250, 0.35);
    }

    :host ::ng-deep .fabric-terminal .p-terminal-command-value,
    :host ::ng-deep .fabric-terminal .p-terminal-welcome-message {
      user-select: text;
    }

    .permission {
      margin: 0 18px;
      padding: 12px 14px;
      border-radius: 10px;
      background: #2b2410;
      border: 1px solid #b78a2b;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
    }

    .permission__what {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }

    .permission__tool {
      font-weight: 600;
      color: #ffd98a;
    }

    .permission__hint {
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
      color: #d8c9a4;
      overflow-wrap: anywhere;
    }

    .permission__actions {
      display: flex;
      gap: 10px;
    }

    /* Comfortable touch targets: this is answered from a phone. */
    .permission__allow,
    .permission__deny {
      min-height: 44px;
      min-width: 116px;
      font-weight: 600;
    }

    .permission__allow {
      background: #15803d;
      border-color: #1a9c4b;
    }

    .permission__deny {
      background: #b91c1c;
      border-color: #d33;
    }

    .runtime-error {
      position: fixed;
      right: 16px;
      bottom: 16px;
      z-index: 1000;
      width: min(calc(100vw - 32px), 760px);
      background: #3a1d1d;
      border: 1px solid #c53030;
      color: #ffd7d7;
      border-radius: 8px;
      padding: 12px;
    }

    .runtime-error pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
    }

    .login-shell {
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #0b1015;
    }

    .login-panel {
      width: min(100%, 380px);
      padding: 24px;
      border-radius: 10px;
      background: #0f1419;
      border: 1px solid #1f2a37;
    }

    .login-panel form {
      flex-direction: column;
      align-items: stretch;
    }

    .login-panel input,
    .login-panel button {
      width: 100%;
    }

    .login-error {
      color: #fca5a5;
      font-size: 13px;
    }

    @media (max-width: 960px) {
      .topbar {
        height: auto;
        align-items: flex-start;
        flex-direction: column;
      }

      .controls {
        width: 100%;
        justify-content: flex-start;
      }

      input {
        width: 100%;
      }

      :host ::ng-deep .fabric-terminal {
        height: calc(100vh - 180px) !important;
      }
    }
  `],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent implements OnDestroy {
  private readonly authService = inject(AuthService);
  private readonly agentService = inject(AgentService);
  private readonly commandService = inject(CommandService);
  private readonly eventStreamService = inject(EventStreamService);
  private readonly runtimeErrorService = inject(RuntimeErrorService);
  private readonly terminalService = inject(TerminalService);
  private readonly hostElement = inject(ElementRef<HTMLElement>);
  private readonly subscriptions = new Subscription();

  readonly session = signal<AuthSession | null>(this.authService.currentSession);
  readonly runtimeError = signal<string | null>(null);
  readonly agents = signal<Agent[]>([]);
  readonly selectedAgentId = signal<string | null>(null);
  readonly selectedAgent = signal<Agent | null>(null);
  readonly powerShellSessionId = signal<string | null>(null);
  readonly workingDirectory = signal('');
  readonly pendingAction = signal(false);
  readonly activeCommandId = signal<string | null>(null);
  readonly terminalError = signal('');
  readonly terminalMounted = signal(true);
  readonly loginUsername = signal('');
  readonly loginPassword = signal('');
  readonly loginError = signal('');
  readonly claudeSessionId = signal<string | null>(null);
  readonly claudePermissionMode = signal(DEFAULT_CLAUDE_PERMISSION_MODE);
  readonly pendingPermission = signal<PendingPermission | null>(null);
  readonly decidingPermission = signal(false);

  private activeCommandSawProgress = false;
  private activeCommandLastSequence = 0;
  private stdoutBuffer = '';
  private stderrBuffer = '';
  private renderedResponse = '';
  private commandHistory = this.loadCommandHistory();
  private historyIndex: number | null = null;
  private historyDraft = '';
  private shouldAutoScroll = true;
  private resumedTerminalState = false;

  constructor() {
    this.restoreTerminalState();

    this.subscriptions.add(
      this.runtimeErrorService.error$.subscribe((message) => {
        this.runtimeError.set(message);
      }),
    );

    effect(() => {
      this.persistTerminalState();
    });

    this.subscriptions.add(
      this.authService.session$.subscribe((session) => {
        this.session.set(session);
        if (session === null) {
          this.eventStreamService.disconnect();
          this.selectedAgentId.set(null);
          this.selectedAgent.set(null);
          this.powerShellSessionId.set(null);
          this.claudeSessionId.set(null);
          this.activeCommandId.set(null);
          this.agents.set([]);
          this.resetStreamingState();
          this.resumedTerminalState = false;
          return;
        }

        this.eventStreamService.connect();
        this.refreshAll();
      }),
    );

    this.subscriptions.add(
      this.agentService.agents$.subscribe((agents) => {
        this.agents.set(agents);
        if (this.selectedAgentId() === null && agents.length > 0) {
          this.selectAgent(agents[0]);
          return;
        }
        const current = this.selectedAgentId();
        if (current !== null) {
          this.selectedAgent.set(agents.find((agent) => agent.id === current) ?? null);
        }
        if (!this.resumedTerminalState && this.selectedAgent() !== null) {
          this.resumedTerminalState = true;
          void this.resumePersistedTerminalContext();
        }
      }),
    );

    this.subscriptions.add(
      this.eventStreamService.events$.subscribe((event) => {
        this.handleRealtimeEvent(event);
      }),
    );

    this.subscriptions.add(
      this.terminalService.commandHandler.subscribe((command) => {
        void this.handleTerminalCommand(command);
      }),
    );
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
  }

  @HostListener('window:keydown', ['$event'])
  onWindowKeydown(event: KeyboardEvent): void {
    if (this.isTerminalInputFocused() && event.key === 'ArrowUp') {
      event.preventDefault();
      this.navigateHistory(-1);
      return;
    }

    if (this.isTerminalInputFocused() && event.key === 'ArrowDown') {
      event.preventDefault();
      this.navigateHistory(1);
      return;
    }

    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c') {
      if (this.hasSelectedTerminalText()) {
        return;
      }
      if (this.activeCommandId() !== null) {
        event.preventDefault();
        this.cancelActiveCommand();
      }
    }
  }

  controlsLocked(): boolean {
    return this.pendingAction() || this.activeCommandId() !== null;
  }

  selectedAgentStatus(): string {
    return this.selectedAgent()?.status ?? 'offline';
  }

  get welcomeMessage(): string {
    const commands = ['clear', 'help', 'open', 'close', 'cancel', 'exit'];
    return [
      'Fabric terminal',
      `Agent: ${this.selectedAgent()?.name ?? 'none'}`,
      `PowerShell session: ${this.powerShellSessionId() === null ? 'closed' : this.promptPath()}`,
      `Local commands: ${commands.join(', ')}`,
      'Claude Code: claude <prompt> · claude:status · claude:new · claude:mode <mode>',
      'Ctrl+C cancels the active remote command.',
    ].join('\n');
  }

  terminalPrompt(): string {
    if (this.pendingPermission() !== null) {
      return 'allow / deny ?';
    }
    const target = this.powerShellSessionId() === null ? 'closed' : this.promptPath();
    const claudeMarker = this.claudeSessionId() === null ? '' : ':claude';
    if (this.activeCommandId() !== null) {
      return `fabric[${target}${claudeMarker}:running] $`;
    }
    return `fabric[${target}${claudeMarker}] $`;
  }

  shouldHidePromptWhileRunning(): boolean {
    // Keep the prompt visible while a permission is pending: it is the only way
    // to answer.
    return this.activeCommandId() !== null && this.pendingPermission() === null;
  }

  displayError(): string | null {
    return this.terminalError() || this.runtimeError();
  }

  hasTerminalOutput(): boolean {
    return this.renderedResponse.length > 0;
  }

  submitLogin(): void {
    this.loginError.set('');
    this.authService.login(this.loginUsername(), this.loginPassword()).subscribe({
      next: () => {
        this.runtimeErrorService.clear();
        this.refreshAll();
      },
      error: (errorResponse: unknown) => {
        this.loginError.set(this.extractHttpError(errorResponse, 'Sign-in failed'));
      },
    });
  }

  logout(): void {
    this.authService.logout().subscribe({
      next: () => {
        this.loginError.set('');
        this.clearTerminalState();
      },
    });
  }

  refreshAll(): void {
    this.agentService.refresh();
    this.commandService.refresh();
  }

  selectAgent(agent: Agent): void {
    this.selectedAgentId.set(agent.id);
    this.selectedAgent.set(agent);
    this.terminalError.set('');
    void this.resetTerminalView();
  }

  selectAgentById(agentId: string): void {
    const agent = this.agents().find((entry) => entry.id === agentId) ?? null;
    this.selectedAgentId.set(agentId);
    this.selectedAgent.set(agent);
    this.powerShellSessionId.set(null);
    this.claudeSessionId.set(null);
    this.activeCommandId.set(null);
    this.resetStreamingState();
    this.terminalError.set('');
    void this.resetTerminalView();
  }

  openSession(): void {
    const agent = this.selectedAgent();
    if (agent === null) {
      return;
    }
    void this.runSessionCommand(agent, 'windows_powershell.session.create', {
      working_directory: this.workingDirectory().trim() || undefined,
    }, false);
  }

  closeSession(): void {
    const agent = this.selectedAgent();
    const sessionId = this.powerShellSessionId();
    if (agent === null || sessionId === null) {
      return;
    }
    void this.runSessionCommand(agent, 'windows_powershell.session.close', {
      session_id: sessionId,
    }, false);
  }

  cancelActiveCommand(): void {
    const commandId = this.activeCommandId();
    if (commandId === null) {
      return;
    }

    this.commandService.cancelCommand(commandId).subscribe({
      error: (error) => {
        const message = this.extractHttpError(error, 'Cancel failed');
        this.terminalError.set(message);
        this.terminalService.sendResponse(message);
      },
    });
  }

  copyTerminalOutput(): void {
    if (this.renderedResponse.length === 0) {
      return;
    }

    void this.writeClipboard(this.renderedResponse).catch((error: unknown) => {
      this.terminalError.set(this.extractHttpError(error, 'Copy failed'));
    });
  }

  private handleRealtimeEvent(event: FabricEvent): void {
    if (event.type === 'agent.updated') {
      this.agentService.refresh();
      return;
    }
    if (event.type === 'command.event') {
      this.handleCommandEvent(event.payload);
      return;
    }
    if (event.type === 'command.permission_request') {
      this.handlePermissionRequest(event.payload);
      return;
    }
    if (event.type === 'command.updated') {
      this.handleCommandUpdated(event.payload);
    }
  }

  /** Claude Code is blocked on this tool call until the operator rules on it. */
  private handlePermissionRequest(payload: Record<string, unknown>): void {
    if (payload['command_id'] !== this.activeCommandId()) {
      return;
    }
    const request = this.asRecord(payload['permission_request']);
    if (request === null || typeof request['request_id'] !== 'string') {
      return;
    }
    if (request['decision'] !== 'pending') {
      return;
    }

    this.pendingPermission.set({
      requestId: request['request_id'],
      toolName: typeof request['tool_name'] === 'string' ? request['tool_name'] : 'tool',
      toolInput: this.asRecord(request['tool_input']) ?? {},
    });
    this.appendTerminalLine(this.describePermissionRequest());
  }

  private describePermissionRequest(): string {
    const pending = this.pendingPermission();
    if (pending === null) {
      return '';
    }
    const hint = this.summarizeToolInput(pending.toolInput);
    return [
      '',
      `[?] Claude requests permission: ${pending.toolName}${hint}`,
      '    allow           - run it',
      '    deny [reason]   - refuse and tell Claude why',
    ].join('\n');
  }

  private summarizeToolInput(input: Record<string, unknown>): string {
    for (const key of ['command', 'file_path', 'path', 'pattern', 'url']) {
      const value = input[key];
      if (typeof value === 'string' && value.trim().length > 0) {
        const firstLine = value.trim().split(/\r?\n/)[0];
        return ` — ${firstLine.slice(0, 160)}`;
      }
    }
    return '';
  }

  /** The tool argument that makes the request decidable, for the card. */
  permissionHint(): string {
    const pending = this.pendingPermission();
    if (pending === null) {
      return '';
    }
    return this.summarizeToolInput(pending.toolInput).replace(/^ — /, '');
  }

  /** Button path. The typed `allow` / `deny` path routes here too. */
  decidePermission(allowed: boolean, reason = ''): void {
    void this.submitPermissionDecision(allowed, reason);
  }

  private async handlePermissionTerminalCommand(command: string): Promise<boolean> {
    if (this.pendingPermission() === null) {
      return false;
    }

    const match = /^(allow|deny|y|n)(?:\s+([\s\S]*))?$/i.exec(command.trim());
    if (match === null) {
      this.terminalService.sendResponse(
        'Claude attend une décision. Répondez `allow` ou `deny [raison]`, ' +
          'ou utilisez les boutons.',
      );
      return true;
    }

    const verb = match[1].toLowerCase();
    await this.submitPermissionDecision(
      verb === 'allow' || verb === 'y',
      (match[2] ?? '').trim(),
    );
    return true;
  }

  private async submitPermissionDecision(
    allowed: boolean,
    reason: string,
  ): Promise<void> {
    const pending = this.pendingPermission();
    const commandId = this.activeCommandId();
    if (pending === null || this.decidingPermission()) {
      return;
    }
    if (commandId === null) {
      this.pendingPermission.set(null);
      return;
    }

    // Clear only after the call succeeds: losing the card on a failed request
    // would leave the turn blocked with no way to answer.
    this.decidingPermission.set(true);
    try {
      await firstValueFrom(
        this.commandService.decidePermission(
          commandId,
          pending.requestId,
          allowed ? 'allow' : 'deny',
          reason,
        ),
      );
      this.pendingPermission.set(null);
      this.appendTerminalLine(
        allowed ? '[autorisé]' : `[refusé]${reason ? ` ${reason}` : ''}`,
      );
    } catch (error) {
      const message = this.extractHttpError(error, "Décision non transmise");
      this.terminalError.set(message);
      this.appendTerminalLine(message);
    } finally {
      this.decidingPermission.set(false);
    }
  }

  private async handleTerminalCommand(command: string): Promise<void> {
    const trimmed = command.trim();
    if (trimmed.length === 0) {
      this.terminalService.sendResponse('');
      return;
    }

    this.recordHistory(trimmed);

    // A blocked Claude turn owns the prompt: nothing else can run until it is
    // answered, so this must come first.
    if (await this.handlePermissionTerminalCommand(trimmed)) {
      return;
    }

    if (await this.handleClaudeTerminalCommand(trimmed)) {
      return;
    }

    if (await this.handleLocalTerminalCommand(trimmed)) {
      return;
    }

    const sessionId = this.powerShellSessionId();
    if (sessionId === null) {
      this.terminalService.sendResponse('Open a session first.');
      return;
    }

    await this.startStreamingCommand(
      POWERSHELL_PROVIDER,
      'windows_powershell.command.run',
      { session_id: sessionId, command: trimmed },
      60,
      'PowerShell command failed',
    );
  }

  /** Route `claude …` lines to the local Claude Code session, not to PowerShell. */
  private async handleClaudeTerminalCommand(command: string): Promise<boolean> {
    const match = /^claude(:[a-zA-Z]+)?(\s[\s\S]*)?$/.exec(command);
    if (match === null) {
      return false;
    }

    const subcommand = (match[1] ?? '').slice(1).toLowerCase();
    const argument = (match[2] ?? '').trim();

    if (subcommand === 'new') {
      this.claudeSessionId.set(null);
      this.terminalService.sendResponse('Claude session reset. The next prompt starts a new one.');
      return true;
    }

    if (subcommand === 'mode') {
      if (!CLAUDE_PERMISSION_MODES.has(argument)) {
        this.terminalService.sendResponse(
          `Unknown permission mode. Use one of: ${[...CLAUDE_PERMISSION_MODES].join(', ')}.`,
        );
        return true;
      }
      this.claudePermissionMode.set(argument);
      this.terminalService.sendResponse(`Claude permission mode set to ${argument}.`);
      return true;
    }

    if (subcommand === 'status') {
      await this.runClaudeSessionStatus();
      return true;
    }

    if (subcommand !== '') {
      this.terminalService.sendResponse(
        'Unknown claude subcommand. Use claude:status, claude:new or claude:mode <mode>.',
      );
      return true;
    }

    if (argument.length === 0) {
      this.terminalService.sendResponse([
        'Usage: claude <prompt>',
        `  session          : ${this.claudeSessionId() ?? 'new on next prompt'}`,
        `  working directory: ${this.workingDirectory() || '(agent default)'}`,
        `  permission mode  : ${this.claudePermissionMode()}`,
        '  claude:status    - probe the local Claude Code installation',
        '  claude:new       - forget the current session id',
        '  claude:mode <m>  - acceptEdits, plan, bypassPermissions, …',
      ].join('\n'));
      return true;
    }

    await this.startStreamingCommand(
      CLAUDE_PROVIDER,
      'claude_code_local.message.send',
      this.buildClaudePayload(argument),
      CLAUDE_TIMEOUT_SECONDS,
      'Claude command failed',
    );
    return true;
  }

  private buildClaudePayload(prompt: string): Record<string, unknown> {
    const workingDirectory = this.workingDirectory().trim();
    const sessionId = this.claudeSessionId();
    return {
      text: prompt,
      permission_mode: this.claudePermissionMode(),
      timeout_seconds: CLAUDE_TIMEOUT_SECONDS,
      ...(workingDirectory.length > 0 ? { working_directory: workingDirectory } : {}),
      ...(sessionId !== null ? { session_id: sessionId } : {}),
    };
  }

  private async runClaudeSessionStatus(): Promise<void> {
    const agent = this.selectedAgent();
    if (agent === null) {
      this.terminalService.sendResponse('No agent selected.');
      return;
    }

    this.pendingAction.set(true);
    try {
      const created = await firstValueFrom(
        this.commandService.createCommand({
          agent_id: agent.id,
          provider: CLAUDE_PROVIDER,
          action: 'claude_code_local.session.status',
          payload: {},
          timeout_seconds: 30,
        }),
      );
      const completed = await this.waitForCommand(created.id);
      if (completed.status !== 'succeeded') {
        throw new Error(completed.error || 'Claude status failed');
      }
      this.terminalService.sendResponse(JSON.stringify(completed.result, null, 2));
    } catch (error) {
      const message = this.extractHttpError(error, 'Claude status failed');
      this.terminalError.set(message);
      this.terminalService.sendResponse(message);
    } finally {
      this.pendingAction.set(false);
    }
  }

  private async startStreamingCommand(
    provider: string,
    action: string,
    payload: Record<string, unknown>,
    timeoutSeconds: number,
    failureLabel: string,
  ): Promise<void> {
    const agent = this.selectedAgent();
    if (agent === null) {
      this.terminalService.sendResponse('No agent selected.');
      return;
    }
    if (this.activeCommandId() !== null) {
      this.terminalService.sendResponse('A command is already running. Press Cancel or Ctrl+C.');
      return;
    }

    this.terminalError.set('');
    this.resetStreamingState();

    try {
      const created = await firstValueFrom(
        this.commandService.createCommand({
          agent_id: agent.id,
          provider,
          action,
          payload,
          timeout_seconds: timeoutSeconds,
        }),
      );
      this.activeCommandId.set(created.id);
      await this.syncActiveCommand(created.id);
    } catch (error) {
      const message = this.extractHttpError(error, failureLabel);
      this.terminalError.set(message);
      this.terminalService.sendResponse(message);
      this.resetStreamingState();
    }
  }

  private async runSessionCommand(
    agent: Agent,
    action: 'windows_powershell.session.create' | 'windows_powershell.session.close',
    payload: Record<string, unknown>,
    announceInTerminal: boolean,
  ): Promise<void> {
    this.pendingAction.set(true);
    this.terminalError.set('');

    try {
      const created = await firstValueFrom(
        this.commandService.createCommand({
          agent_id: agent.id,
          provider: 'windows_powershell',
          action,
          payload,
          timeout_seconds: 60,
        }),
      );

      const completed = await this.waitForCommand(created.id);
      this.captureSessionState(completed);
      if (completed.status !== 'succeeded') {
        throw new Error(completed.error || 'Session command failed');
      }

      if (announceInTerminal) {
        this.terminalService.sendResponse(
          action === 'windows_powershell.session.create'
            ? `Session opened in ${this.workingDirectory()}.`
            : 'Session closed.',
        );
      }
    } catch (error) {
      const message = this.extractHttpError(error, 'Session command failed');
      this.terminalError.set(message);
      if (announceInTerminal) {
        this.terminalService.sendResponse(message);
      }
    } finally {
      this.pendingAction.set(false);
    }
  }

  private async waitForCommand(commandId: string): Promise<Command> {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const command = await firstValueFrom(this.commandService.getCommand(commandId));
      if (FINAL_COMMAND_STATUSES.has(command.status)) {
        return command;
      }
      await new Promise((resolve) => globalThis.setTimeout(resolve, 1000));
    }
    throw new Error('Command timed out while waiting for completion');
  }

  private handleCommandEvent(payload: Record<string, unknown>): void {
    if (payload['command_id'] !== this.activeCommandId()) {
      return;
    }

    const commandEvent = this.asRecord(payload['event']);
    const eventPayload = this.asRecord(commandEvent?.['payload']);
    const sequence = commandEvent?.['sequence'];
    if (eventPayload === null || typeof sequence !== 'number') {
      return;
    }
    if (sequence <= this.activeCommandLastSequence) {
      return;
    }
    this.activeCommandLastSequence = sequence;

    const delta = eventPayload['delta'];
    if (typeof delta !== 'string') {
      return;
    }
    // PowerShell tags each chunk with a stream; Claude deltas are plain text.
    const stream = eventPayload['stream'];

    this.activeCommandSawProgress = true;
    this.flushTerminalDelta(typeof stream === 'string' ? stream : 'stdout', delta);
  }

  private handleCommandUpdated(payload: Record<string, unknown>): void {
    const command = this.asCommand(payload['command']);
    if (command === null || command.id !== this.activeCommandId()) {
      return;
    }

    if (!FINAL_COMMAND_STATUSES.has(command.status)) {
      return;
    }

    this.captureSessionState(command);
    this.flushRemainingTerminalOutput();
    if (this.activeCommandSawProgress) {
      this.renderTerminalCompletion(command);
    } else {
      this.renderTerminalResult(command);
    }
    this.activeCommandId.set(null);
    this.resetStreamingState();
  }

  private async syncActiveCommand(commandId: string): Promise<void> {
    const command = await firstValueFrom(this.commandService.getCommand(commandId));
    if (command.id !== this.activeCommandId()) {
      return;
    }

    for (const event of command.events) {
      this.handleCommandEvent({
        command_id: command.id,
        event,
      });
    }

    // A permission can be raised while the browser is not listening (page
    // reload, dropped socket): recover it from the command itself.
    const pending = (command.permission_requests ?? []).find(
      (request) => request.decision === 'pending',
    );
    if (pending !== undefined) {
      this.handlePermissionRequest({
        command_id: command.id,
        permission_request: pending,
      });
    }

    if (FINAL_COMMAND_STATUSES.has(command.status)) {
      this.handleCommandUpdated({ command });
    }
  }

  private async resumePersistedTerminalContext(): Promise<void> {
    const activeCommandId = this.activeCommandId();
    if (activeCommandId !== null) {
      await this.syncActiveCommand(activeCommandId);
      return;
    }

    const sessionId = this.powerShellSessionId();
    const agent = this.selectedAgent();
    if (sessionId === null || agent === null) {
      return;
    }

    try {
      const created = await firstValueFrom(
        this.commandService.createCommand({
          agent_id: agent.id,
          provider: 'windows_powershell',
          action: 'windows_powershell.session.status',
          payload: { session_id: sessionId },
          timeout_seconds: 20,
        }),
      );
      const completed = await this.waitForCommand(created.id);
      if (completed.status !== 'succeeded') {
        throw new Error(completed.error || 'Session restore failed');
      }
      this.captureSessionState(completed);
    } catch {
      this.powerShellSessionId.set(null);
    }
  }

  private async handleLocalTerminalCommand(command: string): Promise<boolean> {
    const normalized = command.trim().toLowerCase();

    if (normalized === 'help') {
      this.terminalService.sendResponse([
        'Local commands:',
        '  open   - open a PowerShell session',
        '  close  - close the current session',
        '  exit   - same as close',
        '  cancel - cancel the active remote command',
        '  clear  - clear terminal output',
        '  cls    - clear terminal output',
        '',
        'Claude Code (no PowerShell session required):',
        '  claude <prompt>   - send a prompt to the local Claude Code session',
        '  claude            - show the current Claude context',
        '  claude:status     - probe the local Claude Code installation',
        '  claude:new        - start a new Claude session on the next prompt',
        '  claude:mode <m>   - permission mode (default, acceptEdits, plan, …)',
        '',
        'When Claude asks for permission:',
        '  allow             - run the tool call',
        '  deny [reason]     - refuse it and tell Claude why',
        '',
        'Anything else is executed in the PowerShell session.',
      ].join('\n'));
      return true;
    }

    if (normalized === 'clear' || normalized === 'cls') {
      this.terminalError.set('');
      await this.resetTerminalView();
      return true;
    }

    if (normalized === 'cancel') {
      if (this.activeCommandId() === null) {
        this.terminalService.sendResponse('No active command.');
        return true;
      }
      this.cancelActiveCommand();
      return true;
    }

    if (normalized === 'open') {
      if (this.powerShellSessionId() !== null) {
        this.terminalService.sendResponse('Session already open.');
        return true;
      }
      const agent = this.selectedAgent();
      if (agent === null) {
        this.terminalService.sendResponse('No agent selected.');
        return true;
      }
      void this.runSessionCommand(agent, 'windows_powershell.session.create', {
        working_directory: this.workingDirectory().trim() || undefined,
      }, true);
      return true;
    }

    if (normalized === 'close' || normalized === 'exit') {
      if (this.powerShellSessionId() === null) {
        this.terminalService.sendResponse('No session is open.');
        return true;
      }
      const agent = this.selectedAgent();
      const sessionId = this.powerShellSessionId();
      if (agent === null || sessionId === null) {
        this.terminalService.sendResponse('No session is open.');
        return true;
      }
      void this.runSessionCommand(agent, 'windows_powershell.session.close', {
        session_id: sessionId,
      }, true);
      return true;
    }

    return false;
  }

  private flushTerminalDelta(stream: string, delta: string): void {
    if (stream === 'stderr') {
      this.stderrBuffer += delta;
      this.pushTerminalResponse();
      return;
    }

    this.stdoutBuffer += delta;
    this.pushTerminalResponse();
  }

  private flushRemainingTerminalOutput(): void {
    this.pushTerminalResponse();
  }

  private captureSessionState(command: Command): void {
    const result = command.result as Record<string, unknown>;

    if (command.provider === CLAUDE_PROVIDER) {
      // Claude Code returns a fresh session id on every resumed turn: always
      // adopt the latest one or the conversation silently forks.
      if (typeof result['session_id'] === 'string') {
        this.claudeSessionId.set(result['session_id']);
      }
      return;
    }

    if (command.provider !== POWERSHELL_PROVIDER) {
      return;
    }

    const sessionObject = this.asRecord(result['session']);

    if (sessionObject !== null && typeof sessionObject['session_id'] === 'string') {
      this.powerShellSessionId.set(sessionObject['session_id']);
      if (typeof sessionObject['working_directory'] === 'string') {
        this.workingDirectory.set(sessionObject['working_directory']);
      }
    }

    if (typeof result['session_id'] === 'string' && result['closed'] === true) {
      this.powerShellSessionId.set(null);
    }

    const nestedResult = this.asRecord(result['result']);
    if (nestedResult !== null && typeof nestedResult['path'] === 'string') {
      this.workingDirectory.set(nestedResult['path']);
    }
  }

  private renderTerminalCompletion(command: Command): void {
    if (command.status === 'cancelled') {
      this.appendTerminalLine('^C');
      return;
    }
    if (command.status === 'failed' || command.status === 'timed_out') {
      this.appendTerminalLine(command.error || `Command ${command.status}`);
      return;
    }

    const envelope = command.result as Record<string, unknown>;
    const result = this.asRecord(envelope['result']) ?? envelope;
    const exitCode = result['exit_code'];
    if (typeof exitCode === 'number' && exitCode !== 0) {
      this.appendTerminalLine(`[exit ${exitCode}]`);
    }
  }

  private renderTerminalResult(command: Command): void {
    if (command.status !== 'succeeded') {
      this.terminalService.sendResponse(command.error || `Command ${command.status}`);
      return;
    }

    if (command.provider === CLAUDE_PROVIDER) {
      const text = (command.result as Record<string, unknown>)['text'];
      this.renderedResponse = typeof text === 'string' ? text : '';
      this.terminalService.sendResponse(this.renderedResponse || '(no output)');
      return;
    }

    const envelope = command.result as Record<string, unknown>;
    const result = this.asRecord(envelope['result']) ?? envelope;
    const stdout = typeof result['stdout'] === 'string' ? result['stdout'] : '';
    const stderr = typeof result['stderr'] === 'string' ? result['stderr'] : '';
    const exitCode =
      typeof result['exit_code'] === 'number' && result['exit_code'] !== 0
        ? `[exit ${result['exit_code']}]`
        : '';

    const response = [stdout, stderr, exitCode]
      .filter((value) => value.length > 0)
      .join('\n');

    this.renderedResponse = response;
    if (this.renderedResponse.length > 0) {
      this.terminalService.sendResponse(this.renderedResponse);
    }
  }

  private resetStreamingState(): void {
    this.pendingPermission.set(null);
    this.decidingPermission.set(false);
    this.activeCommandSawProgress = false;
    this.activeCommandLastSequence = 0;
    this.stdoutBuffer = '';
    this.stderrBuffer = '';
    this.renderedResponse = '';
  }

  private restoreTerminalState(): void {
    try {
      const raw = globalThis.localStorage.getItem(TERMINAL_STATE_STORAGE_KEY);
      if (raw === null) {
        return;
      }
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      if (typeof parsed['selectedAgentId'] === 'string') {
        this.selectedAgentId.set(parsed['selectedAgentId']);
      }
      if (typeof parsed['powerShellSessionId'] === 'string') {
        this.powerShellSessionId.set(parsed['powerShellSessionId']);
      }
      if (typeof parsed['activeCommandId'] === 'string') {
        this.activeCommandId.set(parsed['activeCommandId']);
      }
      if (typeof parsed['workingDirectory'] === 'string' && parsed['workingDirectory'].length > 0) {
        this.workingDirectory.set(parsed['workingDirectory']);
      }
      if (typeof parsed['claudeSessionId'] === 'string') {
        this.claudeSessionId.set(parsed['claudeSessionId']);
      }
      if (
        typeof parsed['claudePermissionMode'] === 'string' &&
        CLAUDE_PERMISSION_MODES.has(parsed['claudePermissionMode'])
      ) {
        this.claudePermissionMode.set(parsed['claudePermissionMode']);
      }
    } catch {
      this.clearTerminalState();
    }
  }

  private persistTerminalState(): void {
    try {
      globalThis.localStorage.setItem(
        TERMINAL_STATE_STORAGE_KEY,
        JSON.stringify({
          selectedAgentId: this.selectedAgentId(),
          powerShellSessionId: this.powerShellSessionId(),
          activeCommandId: this.activeCommandId(),
          workingDirectory: this.workingDirectory(),
          claudeSessionId: this.claudeSessionId(),
          claudePermissionMode: this.claudePermissionMode(),
        }),
      );
    } catch {
      // Ignore storage failures.
    }
  }

  private clearTerminalState(): void {
    try {
      globalThis.localStorage.removeItem(TERMINAL_STATE_STORAGE_KEY);
    } catch {
      // Ignore storage failures.
    }
  }

  private promptPath(): string {
    const value = this.workingDirectory().trim().replace(/[\\/]+$/, '');
    if (value.length === 0) {
      return 'session';
    }
    // Keep the prompt readable on deep paths without guessing a home directory.
    const parts = value.split(/[\\/]/).filter((part) => part.length > 0);
    return parts.length > 3 ? `…\\${parts.slice(-2).join('\\')}` : value;
  }

  private async resetTerminalView(): Promise<void> {
    this.terminalMounted.set(false);
    await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0));
    this.terminalMounted.set(true);
    await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0));
    this.bindTerminalSelectionGuards();
    this.bindTerminalViewportGuards();
    this.focusTerminalInput();
  }

  private pushTerminalResponse(): void {
    this.renderedResponse = this.composeRenderedResponse();
    if (this.renderedResponse.length > 0) {
      this.terminalService.sendResponse(this.renderedResponse);
      this.scheduleAutoScroll();
    }
  }

  private appendTerminalLine(line: string): void {
    this.renderedResponse = this.composeRenderedResponse();
    this.renderedResponse = this.renderedResponse.length > 0
      ? `${this.renderedResponse}\n${line}`
      : line;
    this.terminalService.sendResponse(this.renderedResponse);
    this.scheduleAutoScroll();
  }

  private composeRenderedResponse(): string {
    const stderr = this.stderrBuffer.length > 0
      ? this.stderrBuffer
          .split(/\r?\n/)
          .map((line) => (line.length > 0 ? `! ${line}` : line))
          .join('\n')
      : '';
    const combined = [this.stdoutBuffer, stderr]
      .filter((value) => value.length > 0)
      .join('');
    return this.trimTerminalOutput(combined);
  }

  private recordHistory(command: string): void {
    if (this.commandHistory[this.commandHistory.length - 1] === command) {
      this.historyIndex = null;
      this.historyDraft = '';
      return;
    }
    this.commandHistory.push(command);
    if (this.commandHistory.length > TERMINAL_HISTORY_LIMIT) {
      this.commandHistory = this.commandHistory.slice(-TERMINAL_HISTORY_LIMIT);
    }
    this.persistCommandHistory();
    this.historyIndex = null;
    this.historyDraft = '';
  }

  private navigateHistory(direction: -1 | 1): void {
    if (this.commandHistory.length === 0) {
      return;
    }

    const input = this.getTerminalInput();
    if (input === null) {
      return;
    }

    if (direction === -1) {
      if (this.historyIndex === null) {
        this.historyDraft = input.value;
        this.historyIndex = this.commandHistory.length - 1;
      } else if (this.historyIndex > 0) {
        this.historyIndex -= 1;
      }
      this.setTerminalInputValue(this.commandHistory[this.historyIndex]);
      return;
    }

    if (this.historyIndex === null) {
      return;
    }
    if (this.historyIndex < this.commandHistory.length - 1) {
      this.historyIndex += 1;
      this.setTerminalInputValue(this.commandHistory[this.historyIndex]);
      return;
    }

    this.historyIndex = null;
    this.setTerminalInputValue(this.historyDraft);
  }

  private hasSelectedTerminalText(): boolean {
    const selection = globalThis.getSelection?.();
    if (selection !== undefined && selection !== null && selection.toString().length > 0) {
      return true;
    }

    const input = this.getTerminalInput();
    if (input === null) {
      return false;
    }

    return (input.selectionStart ?? 0) !== (input.selectionEnd ?? 0);
  }

  private isTerminalInputFocused(): boolean {
    const input = this.getTerminalInput();
    return input !== null && globalThis.document.activeElement === input;
  }

  private focusTerminalInput(): void {
    this.getTerminalInput()?.focus();
  }

  private bindTerminalSelectionGuards(): void {
    const root = this.hostElement.nativeElement.querySelector('.fabric-terminal');
    if (!(root instanceof HTMLElement)) {
      return;
    }

    const selectors = [
      '.p-terminal-command-response',
      '.p-terminal-command-value',
      '.p-terminal-welcome-message',
    ].join(', ');

    for (const element of Array.from(root.querySelectorAll<HTMLElement>(selectors))) {
      if (element.dataset['fabricSelectionGuard'] === 'true') {
        continue;
      }
      element.dataset['fabricSelectionGuard'] = 'true';
      for (const eventName of ['mousedown', 'mouseup', 'click'] as const) {
        element.addEventListener(eventName, (event: Event) => {
          event.stopPropagation();
        });
      }
    }
  }

  private bindTerminalViewportGuards(): void {
    const viewport = this.getTerminalViewport();
    if (viewport === null || viewport.dataset['fabricScrollGuard'] === 'true') {
      return;
    }

    viewport.dataset['fabricScrollGuard'] = 'true';
    viewport.addEventListener('scroll', () => {
      this.shouldAutoScroll = this.isViewportNearBottom(viewport);
    });
    this.shouldAutoScroll = this.isViewportNearBottom(viewport);
  }

  private scheduleAutoScroll(): void {
    if (!this.shouldAutoScroll) {
      return;
    }
    globalThis.setTimeout(() => {
      const viewport = this.getTerminalViewport();
      if (viewport !== null && this.shouldAutoScroll) {
        viewport.scrollTop = viewport.scrollHeight;
      }
    }, 0);
  }

  private getTerminalViewport(): HTMLElement | null {
    return this.hostElement.nativeElement.querySelector(
      '.fabric-terminal .p-terminal-content',
    );
  }

  private isViewportNearBottom(viewport: HTMLElement): boolean {
    const remaining = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    return remaining < 24;
  }

  private getTerminalInput(): HTMLInputElement | null {
    return this.hostElement.nativeElement.querySelector(
      '.fabric-terminal .p-terminal-prompt-value',
    );
  }

  private setTerminalInputValue(value: string): void {
    const input = this.getTerminalInput();
    if (input === null) {
      return;
    }

    input.value = value;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
    input.setSelectionRange(value.length, value.length);
  }

  private trimTerminalOutput(output: string): string {
    if (output.length <= TERMINAL_OUTPUT_MAX_CHARS) {
      return output;
    }
    return TERMINAL_OUTPUT_TRIM_MARKER + output.slice(-TERMINAL_OUTPUT_MAX_CHARS);
  }

  private loadCommandHistory(): string[] {
    try {
      const raw = globalThis.localStorage.getItem(TERMINAL_HISTORY_STORAGE_KEY);
      if (raw === null) {
        return [];
      }
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        return [];
      }
      return parsed.filter((value): value is string => typeof value === 'string');
    } catch {
      return [];
    }
  }

  private persistCommandHistory(): void {
    try {
      globalThis.localStorage.setItem(
        TERMINAL_HISTORY_STORAGE_KEY,
        JSON.stringify(this.commandHistory),
      );
    } catch {
      // Ignore storage failures.
    }
  }

  private async writeClipboard(value: string): Promise<void> {
    if (navigator.clipboard?.writeText !== undefined) {
      await navigator.clipboard.writeText(value);
      return;
    }
    throw new Error('Clipboard API unavailable');
  }

  private asRecord(value: unknown): Record<string, unknown> | null {
    return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null;
  }

  private asCommand(value: unknown): Command | null {
    const record = this.asRecord(value);
    if (record === null || typeof record['id'] !== 'string') {
      return null;
    }
    return record as unknown as Command;
  }

  private extractHttpError(
    errorResponse: unknown,
    fallback = 'Request failed',
  ): string {
    if (errorResponse instanceof Error) {
      return errorResponse.message || fallback;
    }
    if (
      typeof errorResponse === 'object' &&
      errorResponse !== null &&
      'error' in errorResponse
    ) {
      const payload = (errorResponse as { error: unknown }).error;
      return typeof payload === 'string' ? payload : JSON.stringify(payload);
    }
    return fallback;
  }
}

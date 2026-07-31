import { NgFor, NgIf } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  output,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import { NotificationEvent, NotificationTarget } from './models';
import { NotificationTargetService } from './services/notification-target.service';
import { TranslocoPipe, TranslocoService } from '@jsverse/transloco';

interface EventChoice {
  key: NotificationEvent;
  label: string;
  why: string;
}

/**
 * Profile panel for PushIT notification targets.
 *
 * Its own component rather than more lines in the terminal component, which is
 * already too long. Rendered as an overlay because Fabric has no router: the
 * terminal is the app, this is a detour.
 */
@Component({
  selector: 'fabric-notifications-panel',
  standalone: true,
  imports: [FormsModule, NgFor, NgIf, TranslocoPipe],
  template: `
    <div class="scrim" (click)="close.emit()"></div>
    <section class="panel" role="dialog" [attr.aria-label]="'notifications.title' | transloco">
      <header>
        <h2>{{ 'notifications.title' | transloco }}</h2>
        <button type="button" class="ghost" (click)="close.emit()">{{ 'notifications.close' | transloco }}</button>
      </header>

      <p class="intro">
        {{ 'notifications.intro' | transloco }}
      </p>

      <p class="error" *ngIf="error()">{{ error() }}</p>
      <p class="muted" *ngIf="loading()">{{ 'notifications.loading' | transloco }}</p>

      <article class="target" *ngFor="let target of targets(); trackBy: trackById">
        <div class="target__head">
          <input
            class="target__name"
            [ngModel]="target.name"
            (ngModelChange)="patchLocal(target, { name: $event })"
            [placeholder]="'notifications.name' | transloco"
          />
          <span class="badge" *ngIf="target.is_default">{{ 'notifications.isDefault' | transloco }}</span>
        </div>

        <label class="row">
          <span>{{ 'notifications.appToken' | transloco }}</span>
          <input
            [ngModel]="target.app_token"
            (ngModelChange)="patchLocal(target, { app_token: $event })"
            [placeholder]="'notifications.tokenPlaceholder' | transloco"
            autocomplete="off"
            spellcheck="false"
          />
        </label>

        <label class="row">
          <span>{{ 'notifications.notificationTitle' | transloco }}</span>
          <input
            [ngModel]="target.title"
            (ngModelChange)="patchLocal(target, { title: $event })"
            placeholder="Fabric"
          />
        </label>

        <label class="check">
          <input
            type="checkbox"
            [ngModel]="target.enabled"
            (ngModelChange)="patchLocal(target, { enabled: $event })"
          />
          <span>{{ 'notifications.enabledHint' | transloco }}</span>
        </label>

        <fieldset>
          <legend>{{ 'notifications.events' | transloco }}</legend>
          <label class="check" *ngFor="let choice of eventChoices">
            <input
              type="checkbox"
              [ngModel]="target.effective_events[choice.key]"
              (ngModelChange)="toggleEvent(target, choice.key, $event)"
            />
            <span>
              {{ choice.label }}
              <em>{{ choice.why }}</em>
            </span>
          </label>
        </fieldset>

        <div class="target__actions">
          <button
            type="button"
            [disabled]="busy()"
            (click)="save(target)"
          >{{ 'notifications.save' | transloco }}</button>
          <button
            type="button"
            class="ghost"
            *ngIf="!target.is_default"
            [disabled]="busy()"
            (click)="makeDefault(target)"
          >{{ 'notifications.default' | transloco }}</button>
          <button
            type="button"
            class="danger"
            [disabled]="busy()"
            (click)="remove(target)"
          >{{ 'notifications.delete' | transloco }}</button>
        </div>
      </article>

      <p class="muted" *ngIf="!loading() && targets().length === 0">
        {{ 'notifications.empty' | transloco }}
      </p>

      <button type="button" class="add" [disabled]="busy()" (click)="addDraft()">{{ 'notifications.addTarget' | transloco }}</button>
    </section>
  `,
  styles: [`
    .scrim {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.6);
      z-index: 900;
    }

    .panel {
      position: fixed;
      inset: 0 0 0 auto;
      width: min(100vw, 520px);
      z-index: 901;
      overflow-y: auto;
      padding: 18px;
      background: #0f1419;
      border-left: 1px solid #1f2a37;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    h2 {
      margin: 0;
      font-size: 17px;
    }

    .intro,
    .muted {
      margin: 0;
      color: #94a3b8;
      font-size: 13px;
      line-height: 1.5;
    }

    .error {
      margin: 0;
      padding: 8px 10px;
      border-radius: 8px;
      background: #3a1d1d;
      border: 1px solid #c53030;
      color: #ffd7d7;
      font-size: 13px;
      overflow-wrap: anywhere;
    }

    .target {
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 12px;
      border: 1px solid #25384a;
      border-radius: 10px;
      background: #111827;
    }

    .target__head {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .target__name {
      flex: 1;
      font-weight: 600;
    }

    .badge {
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 999px;
      background: #1d4ed8;
      color: #f8fbff;
      white-space: nowrap;
    }

    .row {
      display: flex;
      flex-direction: column;
      gap: 4px;
      font-size: 12px;
      color: #94a3b8;
    }

    input[type='text'],
    input:not([type]) {
      width: 100%;
    }

    input:not([type='checkbox']) {
      min-height: 40px;
      border-radius: 8px;
      border: 1px solid #25384a;
      background: #0b1015;
      color: #d9e2ec;
      padding: 0 10px;
      font: inherit;
    }

    fieldset {
      margin: 0;
      border: 1px solid #25384a;
      border-radius: 8px;
      padding: 10px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    legend {
      font-size: 12px;
      color: #94a3b8;
      padding: 0 4px;
    }

    .check {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      font-size: 13px;
      line-height: 1.4;
    }

    /* Comfortable on a phone, which is where this gets used. */
    .check input {
      min-width: 20px;
      min-height: 20px;
      margin-top: 2px;
    }

    .check em {
      display: block;
      color: #7b8ea3;
      font-size: 12px;
      font-style: normal;
    }

    .target__actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    button {
      min-height: 40px;
      padding: 0 14px;
      border-radius: 8px;
      border: 1px solid #3267c3;
      background: #1d4ed8;
      color: #f8fbff;
      font: inherit;
      cursor: pointer;
    }

    button:disabled {
      opacity: 0.55;
      cursor: default;
    }

    .ghost {
      background: #1f2937;
      border-color: #374151;
    }

    .danger {
      background: #b91c1c;
      border-color: #d33;
    }

    .add {
      align-self: flex-start;
    }

    code {
      font-family: Consolas, 'Courier New', monospace;
    }
  `],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NotificationsPanelComponent implements OnInit {
  private readonly service = inject(NotificationTargetService);
  private readonly transloco = inject(TranslocoService);

  readonly close = output<void>();

  readonly targets = signal<NotificationTarget[]>([]);
  readonly loading = signal(false);
  readonly busy = signal(false);
  readonly error = signal('');

  readonly eventChoices: EventChoice[] = [
    {
      key: 'permission_request',
      label: 'Autorisation demandée',
      why: 'Le tour est bloqué sur la machine en attendant la réponse.',
    },
    {
      key: 'claude_turn_completed',
      label: 'Tour Claude terminé',
      why: 'Avec le début de la réponse.',
    },
    {
      key: 'claude_turn_failed',
      label: 'Tour en échec',
      why: 'Échec, annulation ou expiration.',
    },
    {
      key: 'agent_offline',
      label: 'Agent déconnecté',
      why: 'Seulement si des commandes étaient en cours.',
    },
  ];

  ngOnInit(): void {
    void this.reload();
  }

  trackById(_index: number, target: NotificationTarget): number {
    return target.id;
  }

  /** Local edit only; nothing is sent until "Enregistrer". */
  patchLocal(target: NotificationTarget, patch: Partial<NotificationTarget>): void {
    this.targets.update((targets) =>
      targets.map((entry) => (entry.id === target.id ? { ...entry, ...patch } : entry)),
    );
  }

  toggleEvent(
    target: NotificationTarget,
    key: NotificationEvent,
    value: boolean,
  ): void {
    this.patchLocal(target, {
      events: { ...target.events, [key]: value },
      effective_events: { ...target.effective_events, [key]: value },
    });
  }

  addDraft(): void {
    // Negative id marks a row that does not exist server-side yet.
    const draft: NotificationTarget = {
      id: -Date.now(),
      // Valeur pre-remplie, donc visible : elle se traduit comme le reste.
      name: this.transloco.translate('notifications.defaultTargetName'),
      app_token: '',
      base_url: 'https://pushit-api.foxugly.com',
      title: 'Fabric',
      enabled: true,
      events: {},
      effective_events: {
        permission_request: true,
        claude_turn_completed: true,
        claude_turn_failed: true,
        agent_offline: false,
      },
      is_default: this.targets().length === 0,
      created_at: '',
      updated_at: '',
    };
    this.targets.update((targets) => [...targets, draft]);
  }

  async save(target: NotificationTarget): Promise<void> {
    const draft = {
      name: target.name,
      app_token: target.app_token,
      base_url: target.base_url,
      title: target.title,
      enabled: target.enabled,
      events: target.events,
      is_default: target.is_default,
    };
    await this.run(async () => {
      if (target.id < 0) {
        await firstValueFrom(this.service.create(draft));
      } else {
        await firstValueFrom(this.service.update(target.id, draft));
      }
      await this.reload();
    });
  }

  async makeDefault(target: NotificationTarget): Promise<void> {
    await this.run(async () => {
      await firstValueFrom(this.service.update(target.id, { is_default: true }));
      await this.reload();
    });
  }

  async remove(target: NotificationTarget): Promise<void> {
    if (target.id < 0) {
      this.targets.update((targets) => targets.filter((e) => e.id !== target.id));
      return;
    }
    await this.run(async () => {
      await firstValueFrom(this.service.remove(target.id));
      await this.reload();
    });
  }

  private async reload(): Promise<void> {
    this.loading.set(true);
    try {
      this.targets.set(await firstValueFrom(this.service.list()));
      this.error.set('');
    } catch (error: unknown) {
      this.error.set(this.describe(error, 'Chargement impossible'));
    } finally {
      this.loading.set(false);
    }
  }

  private async run(action: () => Promise<void>): Promise<void> {
    this.busy.set(true);
    this.error.set('');
    try {
      await action();
    } catch (error: unknown) {
      this.error.set(this.describe(error, 'Enregistrement impossible'));
    } finally {
      this.busy.set(false);
    }
  }

  /** Surface the API's field errors rather than a generic message. */
  private describe(error: unknown, fallback: string): string {
    if (typeof error === 'object' && error !== null && 'error' in error) {
      const body = (error as { error: unknown }).error;
      if (typeof body === 'object' && body !== null) {
        const parts = Object.entries(body as Record<string, unknown>).map(
          ([field, detail]) =>
            `${field}: ${Array.isArray(detail) ? detail.join(' ') : String(detail)}`,
        );
        if (parts.length > 0) {
          return parts.join(' · ');
        }
      }
      if (typeof body === 'string' && body.length > 0) {
        return body;
      }
    }
    if (error instanceof Error && error.message) {
      return error.message;
    }
    return fallback;
  }
}

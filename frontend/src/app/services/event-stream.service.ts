import { inject, Injectable, NgZone } from '@angular/core';
import { BehaviorSubject, Observable, Subject } from 'rxjs';

import { FabricEvent } from '../models';
import { ApiBaseService } from './api-base.service';
import { AuthService } from './auth.service';

@Injectable({ providedIn: 'root' })
export class EventStreamService {
  private readonly zone = inject(NgZone);
  private readonly apiBase = inject(ApiBaseService);
  private readonly authService = inject(AuthService);
  private readonly connectionStateSubject = new BehaviorSubject<'idle' | 'connecting' | 'open' | 'error'>('idle');
  private readonly eventsSubject = new Subject<FabricEvent>();
  private socket: WebSocket | null = null;
  private reconnectHandle: number | null = null;

  readonly connectionState$ = this.connectionStateSubject.asObservable();
  readonly events$ = this.eventsSubject.asObservable();
  readonly apiTargetLabel = this.resolveTargetLabel();

  connect(): void {
    const token = this.authService.token;
    if (this.socket !== null || token === null) {
      return;
    }

    const protocol = globalThis.location.protocol === 'https:' ? 'wss' : 'ws';
    const url = this.resolveWebSocketUrl(protocol, token);
    this.connectionStateSubject.next('connecting');
    this.socket = new WebSocket(url);

    this.socket.onopen = () => {
      this.zone.run(() => {
        this.connectionStateSubject.next('open');
      });
    };

    this.socket.onmessage = (event: MessageEvent<string>) => {
      this.zone.run(() => {
        try {
          const payload = JSON.parse(event.data) as FabricEvent;
          this.eventsSubject.next(payload);
        } catch {
          this.connectionStateSubject.next('error');
        }
      });
    };

    this.socket.onerror = () => {
      this.zone.run(() => {
        this.connectionStateSubject.next('error');
      });
    };

    this.socket.onclose = () => {
      this.zone.run(() => {
        this.socket = null;
        if (this.reconnectHandle === null && this.authService.token !== null) {
          this.reconnectHandle = globalThis.setTimeout(() => {
            this.reconnectHandle = null;
            this.connect();
          }, 5000);
        }
      });
    };
  }

  disconnect(): void {
    if (this.reconnectHandle !== null) {
      globalThis.clearTimeout(this.reconnectHandle);
      this.reconnectHandle = null;
    }

    if (this.socket !== null) {
      this.socket.close();
      this.socket = null;
    }
    this.connectionStateSubject.next('idle');
  }

  get events(): Observable<FabricEvent> {
    return this.events$;
  }

  private resolveTargetLabel(): string {
    return this.apiBase.baseUrl.replace('/api/v1', '');
  }

  private resolveWebSocketUrl(protocol: 'ws' | 'wss', token: string): string {
    const apiUrl = new URL(this.apiBase.baseUrl);
    return `${protocol}://${apiUrl.host}/ws/v1/events/?token=${encodeURIComponent(token)}`;
  }
}

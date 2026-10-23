import { HttpClient } from '@angular/common/http';
import { inject, Injectable, NgZone } from '@angular/core';
import { BehaviorSubject, firstValueFrom, Observable, Subject } from 'rxjs';

import { FabricEvent } from '../models';
import { ApiBaseService } from './api-base.service';
import { AuthService } from './auth.service';

interface TicketResponse {
  ticket: string;
  expires_in: number;
}

const RECONNECT_DELAY_MS = 5000;

@Injectable({ providedIn: 'root' })
export class EventStreamService {
  private readonly zone = inject(NgZone);
  private readonly http = inject(HttpClient);
  private readonly apiBase = inject(ApiBaseService);
  private readonly authService = inject(AuthService);
  private readonly connectionStateSubject = new BehaviorSubject<'idle' | 'connecting' | 'open' | 'error'>('idle');
  private readonly eventsSubject = new Subject<FabricEvent>();
  private socket: WebSocket | null = null;
  private opening = false;
  private reconnectHandle: number | null = null;

  readonly connectionState$ = this.connectionStateSubject.asObservable();
  readonly events$ = this.eventsSubject.asObservable();
  readonly apiTargetLabel = this.resolveTargetLabel();

  /**
   * Open the browser event socket. Browsers cannot set headers on a
   * WebSocket, so the socket is authenticated by a single-use ticket (≤ 30 s)
   * obtained with the in-memory access token; no long-lived credential ever
   * appears in the URL (and therefore in access logs).
   */
  connect(): void {
    if (this.socket !== null || this.opening || this.authService.currentSession === null) {
      return;
    }
    this.opening = true;
    this.connectionStateSubject.next('connecting');
    void this.open();
  }

  disconnect(): void {
    if (this.reconnectHandle !== null) {
      globalThis.clearTimeout(this.reconnectHandle);
      this.reconnectHandle = null;
    }

    if (this.socket !== null) {
      const socket = this.socket;
      this.socket = null;
      socket.onclose = null;
      socket.close();
    }
    this.connectionStateSubject.next('idle');
  }

  get events(): Observable<FabricEvent> {
    return this.events$;
  }

  private async open(): Promise<void> {
    let ticket: string;
    try {
      const response = await firstValueFrom(
        this.http.post<TicketResponse>(this.apiBase.buildUrl('/auth/ws-ticket/'), {}),
      );
      ticket = response.ticket;
    } catch {
      this.opening = false;
      this.zone.run(() => this.connectionStateSubject.next('error'));
      this.scheduleReconnect();
      return;
    }
    this.opening = false;
    // Signed out while the ticket was in flight.
    if (this.authService.currentSession === null || this.socket !== null) {
      return;
    }

    const socket = new WebSocket(this.resolveWebSocketUrl(ticket));
    this.socket = socket;

    socket.onopen = () => {
      this.zone.run(() => {
        this.connectionStateSubject.next('open');
      });
    };

    socket.onmessage = (event: MessageEvent<string>) => {
      this.zone.run(() => {
        try {
          const payload = JSON.parse(event.data) as FabricEvent;
          this.eventsSubject.next(payload);
        } catch {
          this.connectionStateSubject.next('error');
        }
      });
    };

    socket.onerror = () => {
      this.zone.run(() => {
        this.connectionStateSubject.next('error');
      });
    };

    socket.onclose = () => {
      this.zone.run(() => {
        if (this.socket === socket) {
          this.socket = null;
        }
        this.scheduleReconnect();
      });
    };
  }

  private scheduleReconnect(): void {
    if (this.reconnectHandle !== null || this.authService.currentSession === null) {
      return;
    }
    this.reconnectHandle = globalThis.setTimeout(() => {
      this.reconnectHandle = null;
      this.connect();
    }, RECONNECT_DELAY_MS);
  }

  private resolveTargetLabel(): string {
    return this.apiBase.baseUrl.replace('/api/v1', '');
  }

  private resolveWebSocketUrl(ticket: string): string {
    const apiUrl = new URL(this.apiBase.baseUrl);
    const protocol = apiUrl.protocol === 'https:' ? 'wss' : 'ws';
    return `${protocol}://${apiUrl.host}/ws/v1/events/?ticket=${encodeURIComponent(ticket)}`;
  }
}

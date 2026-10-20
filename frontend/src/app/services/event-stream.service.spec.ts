import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { ApiBaseService } from './api-base.service';
import { authInterceptor } from './auth.interceptor';
import { AuthService } from './auth.service';
import { EventStreamService } from './event-stream.service';

const USER = { id: 1, username: 'op', is_staff: false, is_superuser: false };

class FakeWebSocket {
  static urls: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(url: string) {
    FakeWebSocket.urls.push(url);
  }
  close(): void {}
}

describe('EventStreamService', () => {
  const realWebSocket = globalThis.WebSocket;

  beforeEach(() => {
    localStorage.clear();
    FakeWebSocket.urls = [];
    (globalThis as { WebSocket: unknown }).WebSocket = FakeWebSocket;
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        {
          provide: ApiBaseService,
          useValue: {
            baseUrl: 'https://fabric.example/api/v1',
            buildUrl: (path: string) => `/api/v1${path}`,
          },
        },
      ],
    });
  });

  afterEach(() => {
    TestBed.inject(EventStreamService).disconnect();
    TestBed.inject(HttpTestingController).verify();
    TestBed.resetTestingModule();
    (globalThis as { WebSocket: unknown }).WebSocket = realWebSocket;
    localStorage.clear();
  });

  it('opens the socket with a single-use ticket, never the access token', async () => {
    const http = TestBed.inject(HttpTestingController);
    const auth = TestBed.inject(AuthService);
    auth.login('op', 'secret-password').subscribe();
    http.expectOne('/api/v1/auth/login/').flush({ mfa_required: true, mfa_token: 'mfa' });
    auth.verifyMfa('123456').subscribe();
    http.expectOne('/api/v1/auth/mfa/verify/').flush({ access: 'access-1', user: USER });

    TestBed.inject(EventStreamService).connect();
    const ticket = http.expectOne('/api/v1/auth/ws-ticket/');
    expect(ticket.request.method).toBe('POST');
    expect(ticket.request.headers.get('Authorization')).toBe('Bearer access-1');
    ticket.flush({ ticket: 'one-shot', expires_in: 30 });
    await Promise.resolve();
    await Promise.resolve();

    expect(FakeWebSocket.urls).toEqual(['wss://fabric.example/ws/v1/events/?ticket=one-shot']);
    expect(FakeWebSocket.urls[0]).not.toContain('access-1');
  });

  it('does not connect without a session', () => {
    TestBed.inject(EventStreamService).connect();

    TestBed.inject(HttpTestingController).expectNone('/api/v1/auth/ws-ticket/');
    expect(FakeWebSocket.urls).toEqual([]);
  });
});

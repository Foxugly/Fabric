import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { ApiBaseService } from './api-base.service';
import { authInterceptor } from './auth.interceptor';
import { AuthService, LEGACY_SESSION_KEY, SESSION_HINT_KEY } from './auth.service';

const USER = { id: 1, username: 'op', is_staff: false, is_superuser: false };

function setup(): { auth: AuthService; http: HttpTestingController } {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(withInterceptors([authInterceptor])),
      provideHttpClientTesting(),
      {
        provide: ApiBaseService,
        useValue: { baseUrl: '/api/v1', buildUrl: (path: string) => `/api/v1${path}` },
      },
    ],
  });
  return { auth: TestBed.inject(AuthService), http: TestBed.inject(HttpTestingController) };
}

describe('AuthService', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    TestBed.inject(HttpTestingController).verify();
    TestBed.resetTestingModule();
    localStorage.clear();
  });

  it('logs in in cookie mode and keeps the access token in memory only', () => {
    const { auth, http } = setup();
    let done = false;

    auth.login('op', 'secret-password').subscribe(() => (done = true));

    const request = http.expectOne('/api/v1/auth/login/');
    expect(request.request.headers.get('X-Auth-Mode')).toBe('cookie');
    expect(request.request.withCredentials).toBe(true);
    expect(request.request.headers.has('Authorization')).toBe(false);
    request.flush({ access: 'access-1', user: USER });

    expect(done).toBe(true);
    expect(auth.accessToken).toBe('access-1');
    expect(auth.currentSession?.user.username).toBe('op');
    // Only a non-secret hint is stored.
    const stored = Object.keys(localStorage).map((key) => localStorage.getItem(key) ?? '');
    expect(stored.some((value) => value.includes('access-1'))).toBe(false);
    expect(localStorage.getItem(SESSION_HINT_KEY)).not.toBeNull();
  });

  it('drops the legacy localStorage token on start', () => {
    localStorage.setItem(LEGACY_SESSION_KEY, JSON.stringify({ token: 'drf', user: USER }));

    setup();

    expect(localStorage.getItem(LEGACY_SESSION_KEY)).toBeNull();
  });

  it('restores the session through the refresh cookie on load', async () => {
    localStorage.setItem(SESSION_HINT_KEY, '1');
    const { auth, http } = setup();

    const restoring = auth.restore();
    const refresh = http.expectOne('/api/v1/auth/refresh/');
    expect(refresh.request.headers.get('X-Auth-Mode')).toBe('cookie');
    expect(refresh.request.withCredentials).toBe(true);
    refresh.flush({ access: 'restored' });
    await Promise.resolve();
    await Promise.resolve();
    const me = http.expectOne('/api/v1/auth/me/');
    expect(me.request.headers.get('Authorization')).toBe('Bearer restored');
    me.flush({ user: USER });
    await restoring;

    expect(auth.accessToken).toBe('restored');
    expect(auth.currentSession?.user.id).toBe(1);
  });

  it('does not call the API on load without a session hint', async () => {
    const { auth } = setup();

    await auth.restore();

    expect(auth.currentSession).toBeNull();
  });

  it.each([401, 403])('ends the session when the refresh is rejected (%i)', async (status) => {
    localStorage.setItem(SESSION_HINT_KEY, '1');
    const { auth, http } = setup();

    const restoring = auth.restore();
    http.expectOne('/api/v1/auth/refresh/').flush(null, { status, statusText: 'Rejected' });
    await restoring;

    expect(auth.currentSession).toBeNull();
    expect(localStorage.getItem(SESSION_HINT_KEY)).toBeNull();
  });

  it('keeps the session hint when the refresh fails for another reason', async () => {
    localStorage.setItem(SESSION_HINT_KEY, '1');
    const { auth, http } = setup();

    const restoring = auth.restore();
    http.expectOne('/api/v1/auth/refresh/').flush(null, { status: 502, statusText: 'Bad Gateway' });
    await restoring;

    expect(auth.currentSession).toBeNull();
    expect(localStorage.getItem(SESSION_HINT_KEY)).toBe('1');
  });

  it('logs out in cookie mode and wipes the terminal history and state', () => {
    const { auth, http } = setup();
    auth.login('op', 'secret-password').subscribe();
    http.expectOne('/api/v1/auth/login/').flush({ access: 'a', user: USER });
    localStorage.setItem('fabric.terminal.history', JSON.stringify(['Get-Content .env']));
    localStorage.setItem('fabric.terminal.state', '{}');
    localStorage.setItem('fabric.lang', 'fr');

    auth.logout().subscribe();
    const request = http.expectOne('/api/v1/auth/logout/');
    expect(request.request.headers.get('X-Auth-Mode')).toBe('cookie');
    expect(request.request.withCredentials).toBe(true);
    request.flush(null, { status: 204, statusText: 'No Content' });

    expect(auth.currentSession).toBeNull();
    expect(auth.accessToken).toBeNull();
    expect(localStorage.getItem('fabric.terminal.history')).toBeNull();
    expect(localStorage.getItem('fabric.terminal.state')).toBeNull();
    expect(localStorage.getItem(SESSION_HINT_KEY)).toBeNull();
    // Unrelated preferences survive.
    expect(localStorage.getItem('fabric.lang')).toBe('fr');
  });

  it('signs out when another tab removes the session hint', () => {
    const { auth, http } = setup();
    auth.login('op', 'secret-password').subscribe();
    http.expectOne('/api/v1/auth/login/').flush({ access: 'a', user: USER });

    window.dispatchEvent(
      new StorageEvent('storage', { key: SESSION_HINT_KEY, oldValue: '1', newValue: null }),
    );

    expect(auth.currentSession).toBeNull();
    expect(auth.accessToken).toBeNull();
  });
});

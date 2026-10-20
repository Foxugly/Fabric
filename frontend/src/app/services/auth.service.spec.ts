import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { ApiBaseService } from './api-base.service';
import { authInterceptor } from './auth.interceptor';
import { AuthService, LEGACY_SESSION_KEY, SESSION_HINT_KEY } from './auth.service';

const USER = { id: 1, username: 'op', is_staff: false, is_superuser: false };
const MFA_TOKEN = 'signed-mfa-token';

/** Password step + TOTP step, the only way to a session. */
function signIn(auth: AuthService, http: HttpTestingController): void {
  auth.login('op', 'secret-password').subscribe();
  http.expectOne('/api/v1/auth/login/').flush({ mfa_required: true, mfa_token: MFA_TOKEN });
  auth.verifyMfa('123456').subscribe();
  http.expectOne('/api/v1/auth/mfa/verify/').flush({ access: 'a', user: USER });
}

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
    sessionStorage.clear();
  });

  afterEach(() => {
    TestBed.inject(HttpTestingController).verify();
    TestBed.resetTestingModule();
    localStorage.clear();
  });

  it('logs in in cookie mode (password + TOTP) and keeps the access token in memory only', () => {
    const { auth, http } = setup();
    let challenge: string | undefined;

    auth.login('op', 'secret-password').subscribe((value) => (challenge = value));

    const request = http.expectOne('/api/v1/auth/login/');
    expect(request.request.headers.get('X-Auth-Mode')).toBe('cookie');
    expect(request.request.withCredentials).toBe(true);
    expect(request.request.headers.has('Authorization')).toBe(false);
    request.flush({ mfa_required: true, mfa_token: MFA_TOKEN });

    expect(challenge).toBe('verify');
    // The password alone signs nobody in.
    expect(auth.currentSession).toBeNull();
    expect(auth.accessToken).toBeNull();
    expect(auth.hasPendingMfa).toBe(true);

    let done = false;
    auth.verifyMfa('123456').subscribe(() => (done = true));
    const verify = http.expectOne('/api/v1/auth/mfa/verify/');
    expect(verify.request.body).toEqual({ mfa_token: MFA_TOKEN, code: '123456' });
    expect(verify.request.headers.get('X-Auth-Mode')).toBe('cookie');
    expect(verify.request.withCredentials).toBe(true);
    expect(verify.request.headers.has('Authorization')).toBe(false);
    verify.flush({ access: 'access-1', user: USER });

    expect(done).toBe(true);
    expect(auth.hasPendingMfa).toBe(false);
    expect(auth.accessToken).toBe('access-1');
    expect(auth.currentSession?.user.username).toBe('op');
    // Only a non-secret hint is stored: neither the access nor the mfa token.
    const stored = Object.keys(localStorage).map((key) => localStorage.getItem(key) ?? '');
    expect(stored.some((value) => value.includes('access-1'))).toBe(false);
    expect(stored.some((value) => value.includes(MFA_TOKEN))).toBe(false);
    expect(localStorage.getItem(SESSION_HINT_KEY)).not.toBeNull();
  });

  it('never stores the mfa token while the second factor is pending', () => {
    const { auth, http } = setup();

    auth.login('op', 'secret-password').subscribe();
    http.expectOne('/api/v1/auth/login/').flush({ mfa_required: true, mfa_token: MFA_TOKEN });

    const stored = Object.keys(localStorage).map((key) => localStorage.getItem(key) ?? '');
    expect(stored.some((value) => value.includes(MFA_TOKEN))).toBe(false);
    expect(sessionStorage.length).toBe(0);
    expect(localStorage.getItem(SESSION_HINT_KEY)).toBeNull();
  });

  it('keeps the challenge pending after a wrong code', () => {
    const { auth, http } = setup();
    auth.login('op', 'secret-password').subscribe();
    http.expectOne('/api/v1/auth/login/').flush({ mfa_required: true, mfa_token: MFA_TOKEN });
    let failed = false;

    auth.verifyMfa('000000').subscribe({ error: () => (failed = true) });
    http
      .expectOne('/api/v1/auth/mfa/verify/')
      .flush({ code: 'mfa_invalid_code' }, { status: 400, statusText: 'Bad Request' });

    expect(failed).toBe(true);
    expect(auth.currentSession).toBeNull();
    expect(auth.hasPendingMfa).toBe(true);
  });

  it('enrols on first sign-in and holds the session until the codes are saved', () => {
    const { auth, http } = setup();
    let challenge: string | undefined;
    auth.login('op', 'secret-password').subscribe((value) => (challenge = value));
    http
      .expectOne('/api/v1/auth/login/')
      .flush({ mfa_setup_required: true, mfa_token: MFA_TOKEN });
    expect(challenge).toBe('setup');

    let secret: string | undefined;
    auth.mfaSetup().subscribe((value) => (secret = value.secret));
    const setupRequest = http.expectOne('/api/v1/auth/mfa/setup/');
    expect(setupRequest.request.body).toEqual({ mfa_token: MFA_TOKEN });
    expect(setupRequest.request.headers.get('X-Auth-Mode')).toBe('cookie');
    setupRequest.flush({
      otpauth_uri: 'otpauth://totp/Fabric:op?secret=ABCD',
      secret: 'ABCD',
      qr_svg: 'data:image/svg+xml,<svg/>',
    });
    expect(secret).toBe('ABCD');

    let codes: string[] = [];
    auth.confirmMfaSetup('654321').subscribe((value) => (codes = value));
    const confirm = http.expectOne('/api/v1/auth/mfa/setup/confirm/');
    expect(confirm.request.body).toEqual({ mfa_token: MFA_TOKEN, code: '654321' });
    confirm.flush({
      access: 'access-2',
      user: { ...USER, mfa_enabled: true },
      recovery_codes: ['AAAAA-BBBBB', 'CCCCC-DDDDD'],
    });

    expect(codes).toEqual(['AAAAA-BBBBB', 'CCCCC-DDDDD']);
    expect(auth.hasPendingMfa).toBe(false);
    // The recovery codes come first: no session yet.
    expect(auth.currentSession).toBeNull();
    expect(localStorage.getItem(SESSION_HINT_KEY)).toBeNull();

    auth.completeEnrolment();

    expect(auth.currentSession?.user.mfa_enabled).toBe(true);
    expect(auth.accessToken).toBe('access-2');
    expect(localStorage.getItem(SESSION_HINT_KEY)).not.toBeNull();
  });

  it('refuses a second-factor call without a pending password step', () => {
    const { auth } = setup();
    let error: unknown;

    auth.verifyMfa('123456').subscribe({ error: (value) => (error = value) });

    expect(error).toBeInstanceOf(Error);
  });

  it('forgets the mfa token when the sign-in is cancelled', () => {
    const { auth, http } = setup();
    auth.login('op', 'secret-password').subscribe();
    http.expectOne('/api/v1/auth/login/').flush({ mfa_required: true, mfa_token: MFA_TOKEN });

    auth.cancelMfa();

    expect(auth.hasPendingMfa).toBe(false);
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
    signIn(auth, http);
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
    signIn(auth, http);

    window.dispatchEvent(
      new StorageEvent('storage', { key: SESSION_HINT_KEY, oldValue: '1', newValue: null }),
    );

    expect(auth.currentSession).toBeNull();
    expect(auth.accessToken).toBeNull();
  });
});

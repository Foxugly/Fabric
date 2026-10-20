import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { ApiBaseService } from './api-base.service';
import { authInterceptor } from './auth.interceptor';
import { AuthService } from './auth.service';

const USER = { id: 1, username: 'op', is_staff: false, is_superuser: false };

describe('authInterceptor', () => {
  let http: HttpClient;
  let httpMock: HttpTestingController;
  let auth: AuthService;

  beforeEach(() => {
    localStorage.clear();
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
    http = TestBed.inject(HttpClient);
    httpMock = TestBed.inject(HttpTestingController);
    auth = TestBed.inject(AuthService);
    auth.login('op', 'secret-password').subscribe();
    httpMock.expectOne('/api/v1/auth/login/').flush({ access: 'old-access', user: USER });
  });

  afterEach(() => {
    httpMock.verify();
    TestBed.resetTestingModule();
    localStorage.clear();
  });

  const expire = () =>
    httpMock.expectOne('/api/v1/agents/').flush(null, { status: 401, statusText: 'Unauthorized' });

  it('attaches the in-memory access token as a Bearer', () => {
    http.get('/api/v1/agents/').subscribe();

    const request = httpMock.expectOne('/api/v1/agents/');
    expect(request.request.headers.get('Authorization')).toBe('Bearer old-access');
    // Ordinary API calls never carry the refresh cookie.
    expect(request.request.withCredentials).toBe(false);
    request.flush([]);
  });

  it('refreshes through the cookie on 401 and replays with the new token', () => {
    let body: unknown;
    http.get('/api/v1/agents/').subscribe((value) => (body = value));

    expire();
    const refresh = httpMock.expectOne('/api/v1/auth/refresh/');
    expect(refresh.request.withCredentials).toBe(true);
    expect(refresh.request.headers.get('X-Auth-Mode')).toBe('cookie');
    expect(refresh.request.headers.has('Authorization')).toBe(false);
    refresh.flush({ access: 'new-access' });
    const retry = httpMock.expectOne('/api/v1/agents/');
    expect(retry.request.headers.get('Authorization')).toBe('Bearer new-access');
    retry.flush([]);

    expect(body).toEqual([]);
    expect(auth.accessToken).toBe('new-access');
    expect(auth.currentSession).not.toBeNull();
  });

  it('shares one refresh between concurrent 401s', () => {
    http.get('/api/v1/agents/').subscribe();
    http.get('/api/v1/commands/').subscribe();

    expire();
    httpMock
      .expectOne('/api/v1/commands/')
      .flush(null, { status: 401, statusText: 'Unauthorized' });
    httpMock.expectOne('/api/v1/auth/refresh/').flush({ access: 'new-access' });
    httpMock.expectOne('/api/v1/agents/').flush([]);
    httpMock.expectOne('/api/v1/commands/').flush([]);
  });

  it.each([401, 403])('clears the session when the refresh is rejected (%i)', (status) => {
    let error: unknown;
    http.get('/api/v1/agents/').subscribe({ error: (err: unknown) => (error = err) });

    expire();
    httpMock.expectOne('/api/v1/auth/refresh/').flush(null, { status, statusText: 'Rejected' });

    expect(error).toBeDefined();
    expect(auth.currentSession).toBeNull();
    expect(auth.accessToken).toBeNull();
  });

  it('keeps the session when the refresh fails with a server error', () => {
    let error: unknown;
    http.get('/api/v1/agents/').subscribe({ error: (err: unknown) => (error = err) });

    expire();
    httpMock
      .expectOne('/api/v1/auth/refresh/')
      .flush(null, { status: 503, statusText: 'Service Unavailable' });

    expect(error).toBeDefined();
    expect(auth.currentSession).not.toBeNull();
  });

  it('leaves other errors alone', () => {
    let error: unknown;
    http.get('/api/v1/agents/').subscribe({ error: (err: unknown) => (error = err) });

    httpMock.expectOne('/api/v1/agents/').flush(null, { status: 500, statusText: 'Boom' });

    expect(error).toBeDefined();
    expect(auth.currentSession).not.toBeNull();
  });
});

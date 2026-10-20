import { HttpClient, HttpContext, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { DestroyRef, Injectable, inject } from '@angular/core';
import {
  BehaviorSubject,
  Observable,
  defer,
  finalize,
  firstValueFrom,
  from,
  map,
  of,
  shareReplay,
  throwError,
  timeout,
} from 'rxjs';
import { catchError, tap } from 'rxjs/operators';

import { AuthSession, FabricUser } from '../models';
import { ApiBaseService } from './api-base.service';
import { SKIP_AUTH, cookieAuthOptions } from './auth-http';

interface LoginResponse {
  access: string;
  user: FabricUser;
}

interface RefreshResponse {
  access: string;
}

interface MeResponse {
  user: FabricUser;
}

/**
 * Non-secret "a session exists" hint in localStorage. It tells a fresh page
 * load whether to try the silent restore, and its `storage` events tell the
 * other tabs "session changed" (new value) or "logged out" (removed). It never
 * carries a token: the refresh token lives in the backend's HttpOnly cookie
 * and the access token in memory only.
 */
export const SESSION_HINT_KEY = 'fabric.session';

/** Pre-JWT storage key (a 7-day DRF token in clear). Deleted on sight. */
export const LEGACY_SESSION_KEY = 'fabric.auth.session';

/** Terminal history and state: typed commands may hold secrets (F-M1). */
export const TERMINAL_STORAGE_PREFIX = 'fabric.terminal.';

/** Web Locks name serialising `auth/refresh/` across every tab of the origin. */
export const REFRESH_LOCK_NAME = 'fabric.refresh';

/** Upper bound on the boot-time restore, so a hung API never blocks the SPA. */
const RESTORE_TIMEOUT_MS = 10_000;

/** 401/403 from `auth/refresh/` = refresh token rejected (expired, blacklisted, absent). */
export function isRejectedRefresh(error: unknown): boolean {
  return error instanceof HttpErrorResponse && (error.status === 401 || error.status === 403);
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = inject(ApiBaseService);

  // Memory only: a reload re-obtains the access token from the refresh cookie.
  // Kept out of `session$` so a token refresh does not look like a new sign-in.
  private access: string | null = null;
  private readonly sessionSubject = new BehaviorSubject<AuthSession | null>(null);
  private refreshRequest$: Observable<string> | null = null;

  readonly session$ = this.sessionSubject.asObservable();

  constructor() {
    removeStorageItem(LEGACY_SESSION_KEY);
    if (typeof window !== 'undefined') {
      const onStorage = (event: StorageEvent) => this.onStorageEvent(event);
      window.addEventListener('storage', onStorage);
      inject(DestroyRef).onDestroy(() => window.removeEventListener('storage', onStorage));
    }
  }

  get currentSession(): AuthSession | null {
    return this.sessionSubject.value;
  }

  get accessToken(): string | null {
    return this.access;
  }

  /** Whether a refresh is worth attempting: a live session or the hint. */
  hasSession(): boolean {
    return this.access !== null || readStorageItem(SESSION_HINT_KEY) !== null;
  }

  login(username: string, password: string): Observable<AuthSession> {
    return this.http
      .post<LoginResponse>(
        this.apiBase.buildUrl('/auth/login/'),
        { username, password },
        { ...cookieAuthOptions(), context: new HttpContext().set(SKIP_AUTH, true) },
      )
      .pipe(
        map((response) => {
          this.access = response.access;
          return { user: response.user };
        }),
        tap((session) => {
          this.sessionSubject.next(session);
          this.announceSessionChange();
        }),
      );
  }

  /**
   * Silent restore at boot (app initializer) and when another tab signs in:
   * refresh through the cookie, then load `/me/`. Never throws. A rejected
   * refresh (401/403) ends the session; a network error / 5xx keeps the hint
   * so the next load retries.
   */
  async restore(): Promise<void> {
    if (!this.hasSession()) {
      return;
    }
    try {
      const access = await firstValueFrom(
        this.refreshAccessToken().pipe(timeout(RESTORE_TIMEOUT_MS)),
      );
      const me = await firstValueFrom(
        this.http
          .get<MeResponse>(this.apiBase.buildUrl('/auth/me/'), {
            headers: new HttpHeaders({ Authorization: `Bearer ${access}` }),
            context: new HttpContext().set(SKIP_AUTH, true),
          })
          .pipe(timeout(RESTORE_TIMEOUT_MS)),
      );
      this.access = access;
      this.sessionSubject.next({ user: me.user });
    } catch (error) {
      if (isRejectedRefresh(error)) {
        this.clearSession();
      } else {
        // Keep the hint, but render as signed out until a later retry.
        this.access = null;
        if (this.sessionSubject.value !== null) {
          this.sessionSubject.next(null);
        }
      }
    }
  }

  /**
   * Obtain a fresh access token from the refresh cookie. Concurrent callers in
   * this tab share one request; across tabs the refresh is serialised with the
   * Web Locks API, so two tabs never present the same cookie (the backend
   * rotates + blacklists it on every refresh: the second would be rejected).
   */
  refreshAccessToken(): Observable<string> {
    if (!this.hasSession()) {
      return throwError(() => new Error('No session to refresh'));
    }
    if (this.refreshRequest$ !== null) {
      return this.refreshRequest$;
    }
    this.refreshRequest$ = defer(() => this.withRefreshLock(() => this.postRefresh())).pipe(
      shareReplay({ bufferSize: 1, refCount: true }),
      finalize(() => {
        this.refreshRequest$ = null;
      }),
    );
    return this.refreshRequest$;
  }

  logout(): Observable<void> {
    return this.http
      .post<void>(
        this.apiBase.buildUrl('/auth/logout/'),
        {},
        { ...cookieAuthOptions(), context: new HttpContext().set(SKIP_AUTH, true) },
      )
      .pipe(
        catchError(() => of(void 0)),
        tap(() => this.clearSession()),
        map(() => void 0),
      );
  }

  /**
   * Drop the local session: access token, session hint, legacy token and the
   * terminal history/state (typed commands may hold secrets). The `storage`
   * event of the removed hint signs the other tabs out too.
   */
  clearSession(): void {
    this.access = null;
    this.sessionSubject.next(null);
    removeStorageItem(SESSION_HINT_KEY);
    removeStorageItem(LEGACY_SESSION_KEY);
    clearTerminalStorage();
  }

  private postRefresh(): Observable<string> {
    return this.http
      .post<RefreshResponse>(
        this.apiBase.buildUrl('/auth/refresh/'),
        {},
        { ...cookieAuthOptions(), context: new HttpContext().set(SKIP_AUTH, true) },
      )
      .pipe(
        map((response) => {
          this.access = response.access;
          return response.access;
        }),
      );
  }

  /** Run `task` under the cross-tab refresh lock; plain call when Web Locks is unavailable. */
  private withRefreshLock<T>(task: () => Observable<T>): Observable<T> {
    const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined;
    if (!locks?.request) {
      return task();
    }
    return from(locks.request(REFRESH_LOCK_NAME, () => firstValueFrom(task())) as Promise<T>);
  }

  private onStorageEvent(event: StorageEvent): void {
    // event.key === null <=> storage.clear() in another tab.
    if (event.key !== null && event.key !== SESSION_HINT_KEY) {
      return;
    }
    if (event.newValue === null) {
      // Logged out elsewhere (the cookie is gone too): drop our copy.
      this.access = null;
      if (this.sessionSubject.value !== null) {
        this.sessionSubject.next(null);
      }
      clearTerminalStorage();
      return;
    }
    // Signed in elsewhere: the shared cookie holds the new session.
    void this.restore();
  }

  /** New hint value -> a `storage` event in every other tab. */
  private announceSessionChange(): void {
    try {
      globalThis.localStorage.setItem(SESSION_HINT_KEY, String(Date.now()));
    } catch {
      // Storage unavailable: the session still works in this tab.
    }
  }
}

function readStorageItem(key: string): string | null {
  try {
    return globalThis.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function removeStorageItem(key: string): void {
  try {
    globalThis.localStorage.removeItem(key);
  } catch {
    // Ignore storage failures.
  }
}

export function clearTerminalStorage(): void {
  try {
    const storage = globalThis.localStorage;
    const keys: string[] = [];
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (key !== null && key.startsWith(TERMINAL_STORAGE_PREFIX)) {
        keys.push(key);
      }
    }
    keys.forEach((key) => storage.removeItem(key));
  } catch {
    // Ignore storage failures.
  }
}

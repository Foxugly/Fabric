import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { BehaviorSubject, Observable, of } from 'rxjs';
import { catchError, tap } from 'rxjs/operators';

import { ApiBaseService } from './api-base.service';
import { AuthSession } from '../models';

interface LoginResponse {
  token: string;
  user: AuthSession['user'];
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly storageKey = 'fabric.auth.session';
  private readonly sessionSubject = new BehaviorSubject<AuthSession | null>(
    this.loadStoredSession(),
  );

  readonly session$ = this.sessionSubject.asObservable();

  constructor(
    private readonly http: HttpClient,
    private readonly apiBase: ApiBaseService,
  ) {}

  get currentSession(): AuthSession | null {
    return this.sessionSubject.value;
  }

  get token(): string | null {
    return this.sessionSubject.value?.token ?? null;
  }

  login(username: string, password: string): Observable<AuthSession> {
    return this.http
      .post<LoginResponse>(this.apiBase.buildUrl('/auth/login/'), {
        username,
        password,
      })
      .pipe(
        tap((response) => {
          const session: AuthSession = {
            token: response.token,
            user: response.user,
          };
          this.persistSession(session);
        }),
      );
  }

  logout(): Observable<void> {
    if (this.token === null) {
      this.clearSession();
      return of(void 0);
    }

    return this.http.post<void>(this.apiBase.buildUrl('/auth/logout/'), {}).pipe(
      catchError(() => of(void 0)),
      tap(() => {
        this.clearSession();
      }),
    );
  }

  clearSession(): void {
    this.sessionSubject.next(null);
    try {
      globalThis.localStorage.removeItem(this.storageKey);
    } catch {
      // Ignore storage failures and keep the in-memory state cleared.
    }
  }

  private persistSession(session: AuthSession): void {
    this.sessionSubject.next(session);
    try {
      globalThis.localStorage.setItem(this.storageKey, JSON.stringify(session));
    } catch {
      // Ignore storage failures and keep the in-memory state only.
    }
  }

  private loadStoredSession(): AuthSession | null {
    try {
      const raw = globalThis.localStorage.getItem(this.storageKey);
      if (raw === null) {
        return null;
      }
      const parsed = JSON.parse(raw) as AuthSession;
      if (typeof parsed.token !== 'string' || parsed.user === undefined) {
        return null;
      }
      return parsed;
    } catch {
      return null;
    }
  }
}

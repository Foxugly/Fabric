import { HttpContextToken, HttpHeaders } from '@angular/common/http';

/**
 * Opt-in to the backend's HttpOnly refresh cookie (backend
 * `apps/api_auth/refresh_cookie.py`, mirrored from PushIT). Sent on the auth
 * calls ONLY: the server then sets the refresh token in a
 * `HttpOnly; Secure; SameSite=Lax` cookie scoped to `/api/v1/auth/` and leaves
 * it out of the JSON body, so no script (and no XSS) can read it.
 * `withCredentials` lets the browser store / send that cookie when the SPA and
 * the API are on different origins (`ng serve` on :4200, API on :8000).
 */
export const AUTH_MODE_HEADER = 'X-Auth-Mode';
export const AUTH_MODE_COOKIE = 'cookie';

export function cookieAuthOptions(): { headers: HttpHeaders; withCredentials: true } {
  return {
    headers: new HttpHeaders({ [AUTH_MODE_HEADER]: AUTH_MODE_COOKIE }),
    withCredentials: true,
  };
}

/** The interceptor leaves the request alone (no Bearer, no refresh-on-401). */
export const SKIP_AUTH = new HttpContextToken<boolean>(() => false);

/** Cleared on the retried request so a second 401 does not loop. */
export const RETRY_ON_AUTH_FAILURE = new HttpContextToken<boolean>(() => true);

import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, switchMap, throwError } from 'rxjs';

import { RETRY_ON_AUTH_FAILURE, SKIP_AUTH } from './auth-http';
import { AuthService, isRejectedRefresh } from './auth.service';

/**
 * Attach the in-memory access token; on a 401, refresh it once through the
 * HttpOnly cookie and replay the request. Only a rejected refresh (401/403)
 * ends the session — a network error or a 5xx (backend restarting during a
 * deploy) keeps it, and the next call retries.
 */
export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const authService = inject(AuthService);

  if (request.context.get(SKIP_AUTH)) {
    return next(request);
  }

  const accessToken = authService.accessToken;
  const authorizedRequest =
    accessToken !== null && !request.headers.has('Authorization')
      ? request.clone({ setHeaders: { Authorization: `Bearer ${accessToken}` } })
      : request;

  return next(authorizedRequest).pipe(
    catchError((error: unknown) => {
      const status = error instanceof HttpErrorResponse ? error.status : null;
      if (status !== 401) {
        return throwError(() => error);
      }
      if (!authService.hasSession() || !authorizedRequest.context.get(RETRY_ON_AUTH_FAILURE)) {
        authService.clearSession();
        return throwError(() => error);
      }

      return authService.refreshAccessToken().pipe(
        switchMap((newAccessToken) =>
          next(
            authorizedRequest.clone({
              setHeaders: { Authorization: `Bearer ${newAccessToken}` },
              context: authorizedRequest.context.set(RETRY_ON_AUTH_FAILURE, false),
            }),
          ),
        ),
        catchError((refreshError: unknown) => {
          if (isRejectedRefresh(refreshError)) {
            authService.clearSession();
          }
          return throwError(() => refreshError);
        }),
      );
    }),
  );
};

import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, throwError } from 'rxjs';

import { AuthService } from './auth.service';

export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const authService = inject(AuthService);
  const token = authService.token;

  if (token === null) {
    return next(request);
  }

  return next(
    request.clone({
      setHeaders: {
        Authorization: `Token ${token}`,
      },
    }),
  ).pipe(
    catchError((error: unknown) => {
      if (
        typeof error === 'object' &&
        error !== null &&
        'status' in error &&
        error.status === 401
      ) {
        authService.clearSession();
      }
      return throwError(() => error);
    }),
  );
};

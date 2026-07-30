import { inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { NotificationTarget, NotificationTargetDraft } from '../models';
import { ApiBaseService } from './api-base.service';

/**
 * The caller's own PushIT targets. Every endpoint is scoped to the authenticated
 * user server-side, so there is nothing to filter here.
 */
@Injectable({ providedIn: 'root' })
export class NotificationTargetService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = inject(ApiBaseService);

  list(): Observable<NotificationTarget[]> {
    return this.http.get<NotificationTarget[]>(this.url());
  }

  create(draft: Partial<NotificationTargetDraft>): Observable<NotificationTarget> {
    return this.http.post<NotificationTarget>(this.url(), draft);
  }

  update(
    id: number,
    draft: Partial<NotificationTargetDraft>,
  ): Observable<NotificationTarget> {
    return this.http.patch<NotificationTarget>(this.url(id), draft);
  }

  remove(id: number): Observable<void> {
    return this.http.delete<void>(this.url(id));
  }

  private url(id?: number): string {
    const path = id === undefined ? '/notification-targets/' : `/notification-targets/${id}/`;
    return this.apiBase.buildUrl(path);
  }
}

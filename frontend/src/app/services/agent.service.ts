import { inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { merge, Observable, of, Subject, timer } from 'rxjs';
import { shareReplay, switchMap } from 'rxjs/operators';

import { Agent } from '../models';
import { ApiBaseService } from './api-base.service';
import { AuthService } from './auth.service';

@Injectable({ providedIn: 'root' })
export class AgentService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = inject(ApiBaseService);
  private readonly authService = inject(AuthService);
  private readonly refreshSubject = new Subject<void>();

  readonly agents$ = this.authService.session$.pipe(
    switchMap((session) =>
      session === null
        ? of<Agent[]>([])
        : merge(timer(0, 5000), this.refreshSubject).pipe(
            switchMap(() => this.listAgents()),
          ),
    ),
    shareReplay({ bufferSize: 1, refCount: true }),
  );

  listAgents(): Observable<Agent[]> {
    return this.http.get<Agent[]>(this.apiBase.buildUrl('/agents/'));
  }

  refresh(): void {
    this.refreshSubject.next();
  }
}

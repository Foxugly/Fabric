import { inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { merge, Observable, of, Subject, timer } from 'rxjs';
import { shareReplay, switchMap } from 'rxjs/operators';

import { Command, CreateCommandRequest } from '../models';
import { ApiBaseService } from './api-base.service';
import { AuthService } from './auth.service';

@Injectable({ providedIn: 'root' })
export class CommandService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = inject(ApiBaseService);
  private readonly authService = inject(AuthService);
  private readonly refreshSubject = new Subject<void>();

  readonly commands$ = this.authService.session$.pipe(
    switchMap((session) =>
      session === null
        ? of<Command[]>([])
        : merge(timer(0, 3000), this.refreshSubject).pipe(
            switchMap(() => this.listCommands()),
          ),
    ),
    shareReplay({ bufferSize: 1, refCount: true }),
  );

  listCommands(): Observable<Command[]> {
    return this.http.get<Command[]>(this.apiBase.buildUrl('/commands/'));
  }

  getCommand(commandId: string): Observable<Command> {
    return this.http.get<Command>(this.apiBase.buildUrl(`/commands/${commandId}/`));
  }

  createCommand(request: CreateCommandRequest): Observable<Command> {
    return this.http.post<Command>(this.apiBase.buildUrl('/commands/'), request);
  }

  cancelCommand(commandId: string): Observable<Command | { id: string; status: string }> {
    return this.http.post<Command | { id: string; status: string }>(
      this.apiBase.buildUrl(`/commands/${commandId}/cancel/`),
      {},
    );
  }

  refresh(): void {
    this.refreshSubject.next();
  }
}

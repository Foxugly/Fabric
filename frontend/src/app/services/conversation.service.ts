import { inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { merge, Observable, of, Subject, timer } from 'rxjs';
import { shareReplay, switchMap } from 'rxjs/operators';

import { Conversation, CreateConversationRequest } from '../models';
import { ApiBaseService } from './api-base.service';
import { AuthService } from './auth.service';

@Injectable({ providedIn: 'root' })
export class ConversationService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = inject(ApiBaseService);
  private readonly authService = inject(AuthService);
  private readonly refreshSubject = new Subject<void>();

  readonly conversations$ = this.authService.session$.pipe(
    switchMap((session) =>
      session === null
        ? of<Conversation[]>([])
        : merge(timer(0, 5000), this.refreshSubject).pipe(
            switchMap(() => this.listConversations()),
          ),
    ),
    shareReplay({ bufferSize: 1, refCount: true }),
  );

  listConversations(): Observable<Conversation[]> {
    return this.http.get<Conversation[]>(this.apiBase.buildUrl('/conversations/'));
  }

  createConversation(
    request: CreateConversationRequest,
  ): Observable<Conversation> {
    return this.http.post<Conversation>(
      this.apiBase.buildUrl('/conversations/'),
      request,
    );
  }

  refresh(): void {
    this.refreshSubject.next();
  }
}

import { inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import {
  CreateMessageRequest,
  CreateMessageResponse,
  Message,
} from '../models';
import { ApiBaseService } from './api-base.service';

@Injectable({ providedIn: 'root' })
export class MessageService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = inject(ApiBaseService);

  listMessages(conversationId: string): Observable<Message[]> {
    return this.http.get<Message[]>(
      this.apiBase.buildUrl(`/conversations/${conversationId}/messages/`),
    );
  }

  createMessage(
    conversationId: string,
    request: CreateMessageRequest,
  ): Observable<CreateMessageResponse> {
    return this.http.post<CreateMessageResponse>(
      this.apiBase.buildUrl(`/conversations/${conversationId}/messages/`),
      request,
    );
  }
}

import { Injectable } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class ApiBaseService {
  readonly baseUrl = this.resolveBaseUrl();

  buildUrl(path: string): string {
    return `${this.baseUrl}${path}`;
  }

  private resolveBaseUrl(): string {
    const globalWindow = globalThis as typeof globalThis & {
      __fabricApiBaseUrl?: string;
    };
    return globalWindow.__fabricApiBaseUrl ?? 'http://127.0.0.1:8000/api/v1';
  }
}

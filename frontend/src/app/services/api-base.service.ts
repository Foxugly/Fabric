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
    const override = globalWindow.__fabricApiBaseUrl;
    if (override !== undefined && override.length > 0) {
      return override;
    }

    // `ng serve` runs on :4200 and talks to the backend on :8000. A deployed
    // build is served by the same origin as the API, so it needs no config —
    // set window.__fabricApiBaseUrl in index.html only for other topologies.
    const { protocol, hostname, port, origin } = globalThis.location;
    if (port === '4200') {
      return `${protocol}//${hostname}:8000/api/v1`;
    }
    return `${origin}/api/v1`;
  }
}

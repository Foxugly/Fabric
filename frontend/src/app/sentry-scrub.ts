/**
 * Retire des événements Sentry les secrets et les contenus du terminal
 * (OPERATIONS.md §3.25, motif de PushIT_frontend `sentry-scrub.ts`).
 *
 * - Paramètres d'URL `ticket` / `token` / `access` / `refresh` / `code` : le
 *   ticket du WebSocket voyage dans l'URL de `/ws/v1/events/`.
 * - En-têtes `Authorization` / `Cookie` et cookies de la requête.
 * - Tout JWT (`eyJ….….…`) et toute valeur `Bearer …` dans les messages, les
 *   exceptions et les breadcrumbs : le jeton d'accès vit en mémoire et peut
 *   apparaître dans une erreur HTTP sérialisée.
 * - Le corps des requêtes (`request.data`) et `extra` : un terminal y mettrait
 *   ce que l'opérateur a tapé et ce que Claude a répondu.
 */
import type { Breadcrumb, Event } from '@sentry/angular';

export const FILTERED = '[Filtered]';

const SENSITIVE_QUERY = /([?&](?:ticket|token|access|refresh|code)=)[^&#\s"']*/gi;
const JWT = /\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g;
const BEARER = /(Bearer\s+)[A-Za-z0-9._~+/=-]+/gi;
const SENSITIVE_HEADERS = new Set(['authorization', 'cookie', 'set-cookie', 'x-auth-mode']);

/** Masque les paramètres, JWT et valeurs Bearer d'une chaîne. */
export function scrubText(value: string): string;
export function scrubText(value: string | undefined): string | undefined;
export function scrubText(value: string | undefined): string | undefined {
  if (!value) {
    return value;
  }
  return value
    .replace(SENSITIVE_QUERY, (_match, prefix: string) => `${prefix}${FILTERED}`)
    .replace(JWT, FILTERED)
    .replace(BEARER, (_match, prefix: string) => `${prefix}${FILTERED}`);
}

function scrubRecord(data: Record<string, unknown> | undefined): void {
  if (!data) {
    return;
  }
  for (const [key, value] of Object.entries(data)) {
    if (typeof value === 'string') {
      data[key] = scrubText(value);
    }
  }
}

export function scrubBreadcrumb(breadcrumb: Breadcrumb): Breadcrumb | null {
  // Console output mirrors the terminal: never shipped.
  if (breadcrumb.category === 'console') {
    return null;
  }
  scrubRecord(breadcrumb.data);
  if (breadcrumb.message) {
    breadcrumb.message = scrubText(breadcrumb.message);
  }
  return breadcrumb;
}

/** Nettoie les champs communs aux erreurs et aux transactions. */
export function scrubEvent<T extends Event>(event: T): T {
  if (event.request) {
    event.request.url = scrubText(event.request.url);
    if (typeof event.request.query_string === 'string') {
      event.request.query_string = scrubText(`?${event.request.query_string}`).slice(1);
    }
    if (event.request.headers) {
      for (const name of Object.keys(event.request.headers)) {
        if (SENSITIVE_HEADERS.has(name.toLowerCase())) {
          event.request.headers[name] = FILTERED;
        } else {
          event.request.headers[name] = scrubText(event.request.headers[name]);
        }
      }
    }
    delete event.request.cookies;
    delete event.request.data;
  }
  delete event.extra;
  if (event.message) {
    event.message = scrubText(event.message);
  }
  if (event.transaction) {
    event.transaction = scrubText(event.transaction);
  }
  for (const exception of event.exception?.values ?? []) {
    exception.value = scrubText(exception.value);
  }
  if (event.breadcrumbs) {
    event.breadcrumbs = event.breadcrumbs
      .map((breadcrumb) => scrubBreadcrumb(breadcrumb))
      .filter((breadcrumb): breadcrumb is Breadcrumb => breadcrumb !== null);
  }
  for (const span of event.spans ?? []) {
    if (span.description) {
      span.description = scrubText(span.description);
    }
    scrubRecord(span.data as Record<string, unknown> | undefined);
  }
  scrubRecord(event.contexts?.trace?.data as Record<string, unknown> | undefined);
  return event;
}

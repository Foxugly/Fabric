import { ErrorHandler, Injectable, Provider } from '@angular/core';

import { scrubBreadcrumb, scrubEvent } from './sentry-scrub';

/**
 * Sentry wiring driven by `<meta>` tags in index.html.
 *
 * The values are substituted at deploy time from `/run/fabric/.env`
 * (deploy/ssm-remote.sh) rather than baked into the bundle, so the same
 * artifact can be promoted between environments. Meta tags rather than an
 * injected `<script>`: the CSP forbids inline scripts.
 *
 * In development the placeholders are still in place, so Sentry stays off.
 *
 * `@sentry/angular` is loaded with a dynamic `import()` so it stays out of the
 * initial bundle. Errors raised before the SDK is ready are queued on the
 * loading promise and sent once it resolves.
 */

type SentrySdk = typeof import('./sentry-sdk');

const PLACEHOLDER_PREFIX = '__FABRIC_';

let sentryReady: Promise<SentrySdk | null> = Promise.resolve(null);

function metaContent(name: string): string {
  const element = globalThis.document?.querySelector<HTMLMetaElement>(
    `meta[name="${name}"]`,
  );
  const content = element?.content?.trim() ?? '';
  // An unsubstituted placeholder means "not configured", not a literal value.
  return content.startsWith(PLACEHOLDER_PREFIX) ? '' : content;
}

/**
 * Starts loading the SDK when a DSN is configured. Returns whether Sentry is
 * enabled; the SDK itself arrives asynchronously.
 */
export function initSentry(): boolean {
  const dsn = metaContent('fabric-sentry-dsn');
  if (dsn.length === 0) {
    return false;
  }

  // Until the SDK has installed its own global handlers, keep what would
  // otherwise be lost during startup.
  const early: unknown[] = [];
  const onError = (event: ErrorEvent): void => {
    early.push(event.error ?? event.message);
  };
  const onRejection = (event: PromiseRejectionEvent): void => {
    early.push(event.reason);
  };
  globalThis.addEventListener('error', onError);
  globalThis.addEventListener('unhandledrejection', onRejection);

  sentryReady = import('./sentry-sdk')
    .then((Sentry) => {
      Sentry.init({
        dsn,
        environment: metaContent('fabric-sentry-env') || 'unknown',
        release: metaContent('fabric-sentry-release') || undefined,
        // The terminal streams whatever the operator typed and whatever Claude
        // answered. None of that belongs in an error tracker.
        // SDK 11 replaced `sendDefaultPii: false` with `dataCollection`, whose
        // defaults are MORE permissive (user/IP, cookies, bodies): pin the v10
        // restrictive baseline explicitly.
        dataCollection: {
          userInfo: false,
          cookies: false,
          httpHeaders: {
            request: { deny: ['forwarded', '-ip', 'remote-', 'via', '-user'] },
            response: { deny: ['forwarded', '-ip', 'remote-', 'via', '-user'] },
          },
          httpBodies: [],
          urlQueryParams: { deny: ['forwarded', '-ip', 'remote-', 'via', '-user'] },
          genAI: { inputs: false, outputs: false },
          databaseQueryData: false,
          queues: false,
          graphQL: { document: false, variables: false },
        },
        tracesSampleRate: 0,
        // Tickets, JWT, Bearer values, request bodies and console breadcrumbs
        // are scrubbed before anything leaves the browser (sentry-scrub.ts).
        beforeSend: (event) => scrubEvent(event),
        beforeSendTransaction: (event) => scrubEvent(event),
        beforeBreadcrumb: (breadcrumb) => scrubBreadcrumb(breadcrumb),
      });
      globalThis.removeEventListener('error', onError);
      globalThis.removeEventListener('unhandledrejection', onRejection);
      for (const error of early.splice(0)) {
        Sentry.captureException(error);
      }
      return Sentry;
    })
    .catch((error: unknown) => {
      globalThis.removeEventListener('error', onError);
      globalThis.removeEventListener('unhandledrejection', onRejection);
      console.error('Sentry failed to load', error);
      return null;
    });
  return true;
}

/** Sends an error to Sentry once the SDK is loaded; no-op when disabled. */
export function captureException(error: unknown): void {
  void sentryReady.then((Sentry) => {
    Sentry?.captureException(error);
  });
}

/**
 * Logs like Angular's default handler, then hands the error to Sentry's own
 * Angular handler (which unwraps HttpErrorResponse and friends) once loaded.
 */
@Injectable()
export class SentryErrorHandler extends ErrorHandler {
  private sentryHandler: Promise<ErrorHandler | null> = sentryReady.then((Sentry) =>
    Sentry ? Sentry.createErrorHandler({ logErrors: false }) : null,
  );

  override handleError(error: unknown): void {
    super.handleError(error);
    void this.sentryHandler.then((handler) => handler?.handleError(error));
  }
}

export function sentryProviders(): Provider[] {
  return [{ provide: ErrorHandler, useClass: SentryErrorHandler }];
}

import { ErrorHandler, Provider } from '@angular/core';
import * as Sentry from '@sentry/angular';

/**
 * Sentry wiring driven by `<meta>` tags in index.html.
 *
 * The values are substituted at deploy time from `/run/fabric/.env`
 * (deploy/ssm-remote.sh) rather than baked into the bundle, so the same
 * artifact can be promoted between environments. Meta tags rather than an
 * injected `<script>`: the CSP forbids inline scripts.
 *
 * In development the placeholders are still in place, so Sentry stays off.
 */

const PLACEHOLDER_PREFIX = '__FABRIC_';

function metaContent(name: string): string {
  const element = globalThis.document?.querySelector<HTMLMetaElement>(
    `meta[name="${name}"]`,
  );
  const content = element?.content?.trim() ?? '';
  // An unsubstituted placeholder means "not configured", not a literal value.
  return content.startsWith(PLACEHOLDER_PREFIX) ? '' : content;
}

export function initSentry(): boolean {
  const dsn = metaContent('fabric-sentry-dsn');
  if (dsn.length === 0) {
    return false;
  }

  Sentry.init({
    dsn,
    environment: metaContent('fabric-sentry-env') || 'unknown',
    release: metaContent('fabric-sentry-release') || undefined,
    // The terminal streams whatever the operator typed and whatever Claude
    // answered. None of that belongs in an error tracker.
    sendDefaultPii: false,
    tracesSampleRate: 0,
    beforeBreadcrumb: (breadcrumb) =>
      breadcrumb.category === 'console' ? null : breadcrumb,
  });
  return true;
}

export function sentryProviders(): Provider[] {
  return [{ provide: ErrorHandler, useValue: Sentry.createErrorHandler() }];
}

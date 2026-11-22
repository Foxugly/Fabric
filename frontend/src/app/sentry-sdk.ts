/**
 * The only `@sentry/angular` entry points Fabric uses. Loaded through a dynamic
 * `import()` (sentry.ts) so the SDK stays out of the initial bundle; named
 * re-exports rather than `import('@sentry/angular')` so the lazy chunk is
 * still tree-shaken down to what is used.
 */
export { captureException, createErrorHandler, init } from '@sentry/angular';

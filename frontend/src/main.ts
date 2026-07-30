import { bootstrapApplication } from '@angular/platform-browser';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { providePrimeNG } from 'primeng/config';
import Aura from '@primeuix/themes/aura';

import { AppComponent } from './app/app.component';
import { initSentry, sentryProviders } from './app/sentry';
import { authInterceptor } from './app/services/auth.interceptor';

// Before bootstrap, so a failure during startup is still reported.
const sentryEnabled = initSentry();

const reportRuntimeProblem = (message: string): void => {
  console.error(message);
  const fallback = globalThis.document.getElementById('fabric-bootstrap-fallback');
  if (fallback !== null) {
    fallback.textContent = message;
  }
  const overlay = globalThis.document.getElementById('fabric-runtime-error');
  if (overlay !== null) {
    overlay.textContent = message;
    overlay.style.display = 'block';
  }
};

globalThis.addEventListener('error', (event: ErrorEvent) => {
  reportRuntimeProblem(
    `Runtime error: ${event.message || 'Unknown error'}${
      event.filename ? ` @ ${event.filename}:${event.lineno}` : ''
    }`,
  );
});

globalThis.addEventListener('unhandledrejection', (event: PromiseRejectionEvent) => {
  const reason =
    event.reason instanceof Error
      ? `${event.reason.name}: ${event.reason.message}`
      : String(event.reason);
  reportRuntimeProblem(`Unhandled promise rejection: ${reason}`);
});

bootstrapApplication(AppComponent, {
  providers: [
    provideHttpClient(withInterceptors([authInterceptor])),
    providePrimeNG({
      theme: {
        preset: Aura,
      },
    }),
    ...(sentryEnabled ? sentryProviders() : []),
  ],
}).catch((error: unknown) => {
  reportRuntimeProblem(
    `Failed to bootstrap Fabric frontend: ${
      error instanceof Error ? error.message : String(error)
    }`,
  );
});

import { bootstrapApplication } from '@angular/platform-browser';
import { inject, provideAppInitializer, isDevMode } from '@angular/core';
import { provideTransloco } from '@jsverse/transloco';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { providePrimeNG } from 'primeng/config';
import Aura from '@primeuix/themes/aura';

import { AppComponent } from './app/app.component';
import { initSentry, sentryProviders } from './app/sentry';
import { authInterceptor } from './app/services/auth.interceptor';
import { AVAILABLE_LANGS, DEFAULT_LANG, FALLBACK_LANG } from './app/i18n/available-languages';
import { LanguageService } from './app/i18n/language.service';
import { TranslocoHttpLoader } from './app/i18n/transloco-loader';

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
    // i18n : Transloco, 5 langues, catalogues dans public/i18n/
    // (STANDARD-frontend-layout.md §5 et §5bis). Fabric n'en avait aucun.
    provideTransloco({
      config: {
        availableLangs: [...AVAILABLE_LANGS],
        defaultLang: DEFAULT_LANG,
        fallbackLang: FALLBACK_LANG,
        reRenderOnLangChange: true,
        prodMode: !isDevMode(),
      },
      loader: TranslocoHttpLoader,
    }),
    // Applique la langue retenue avant le premier rendu.
    provideAppInitializer(() => {
      inject(LanguageService).init();
    }),
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

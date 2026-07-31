import { Injectable, inject, signal } from '@angular/core';
import { TranslocoService } from '@jsverse/transloco';

import { AVAILABLE_LANGS, AppLang, DEFAULT_LANG } from './available-languages';

const STORAGE_KEY = 'fabric.language';

/**
 * Autorite de langue de l'app : detient le signal `active`, le persiste et
 * garde Transloco synchronise.
 *
 * L'app n'a pas de compte utilisateur porteur d'une langue, donc la preference
 * vit uniquement dans le navigateur : localStorage d'abord, langue du navigateur
 * ensuite, defaut de la flotte en dernier recours.
 */
@Injectable({ providedIn: 'root' })
export class LanguageService {
  private readonly transloco = inject(TranslocoService);
  readonly active = signal<AppLang>(this.readInitial());

  init(): void {
    this.transloco.setActiveLang(this.active());
  }

  set(lang: AppLang): void {
    this.active.set(lang);
    this.transloco.setActiveLang(lang);
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      /* navigation privee : la preference ne survit pas a l'onglet, sans plus */
    }
  }

  private readInitial(): AppLang {
    try {
      const stored = localStorage.getItem(STORAGE_KEY) as AppLang | null;
      if (stored !== null && AVAILABLE_LANGS.includes(stored)) {
        return stored;
      }
    } catch {
      /* idem */
    }
    const nav = (navigator.language || '').slice(0, 2).toLowerCase() as AppLang;
    return AVAILABLE_LANGS.includes(nav) ? nav : DEFAULT_LANG;
  }
}

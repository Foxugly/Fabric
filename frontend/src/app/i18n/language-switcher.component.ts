import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { NgFor } from '@angular/common';
import { TranslocoPipe } from '@jsverse/transloco';

import { AVAILABLE_LANGS, AppLang, LANG_LABELS } from './available-languages';
import { LanguageService } from './language.service';

/**
 * Sélecteur de langue (STANDARD-frontend-layout.md §5).
 *
 * Le standard impose un trigger qui affiche **uniquement le code langue** en
 * majuscules — jamais de globe ni de drapeau. Fabric n'a pas de shell PrimeNG
 * ni de topmenu : on reste donc sur un `<select>` natif plutôt que d'importer
 * une popup pour un seul contrôle. Il porte le code en majuscules, respecte la
 * règle et reste accessible au clavier sans code supplémentaire.
 */
@Component({
  selector: 'app-language-switcher',
  standalone: true,
  imports: [NgFor, TranslocoPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <select
      class="language-switcher"
      [attr.aria-label]="'language.switch' | transloco"
      [value]="language.active()"
      (change)="onChange($event)"
    >
      <option *ngFor="let lang of langs" [value]="lang" [title]="labels[lang]">
        {{ lang.toUpperCase() }}
      </option>
    </select>
  `,
  styles: [
    `
      .language-switcher {
        min-width: 4rem;
      }
    `,
  ],
})
export class LanguageSwitcherComponent {
  protected readonly language = inject(LanguageService);
  protected readonly langs = AVAILABLE_LANGS;
  protected readonly labels = LANG_LABELS;

  protected onChange(event: Event): void {
    this.language.set((event.target as HTMLSelectElement).value as AppLang);
  }
}

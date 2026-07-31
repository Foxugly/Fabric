/**
 * Langues d'interface — source unique (STANDARD-frontend-layout.md §5).
 * Ajouter une langue = une entree ici + un catalogue dans public/i18n/.
 */
export const AVAILABLE_LANGS = ['fr', 'nl', 'en', 'it', 'es'] as const;
export type AppLang = (typeof AVAILABLE_LANGS)[number];

export const DEFAULT_LANG: AppLang = 'fr';
export const FALLBACK_LANG: AppLang = 'en';

export const LANG_LABELS: Record<AppLang, string> = {
  fr: 'Français',
  nl: 'Nederlands',
  en: 'English',
  it: 'Italiano',
  es: 'Español',
};

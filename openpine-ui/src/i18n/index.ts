/**
 * vue-i18n setup for OpenPine UI.
 *
 * - Default locale: English (per product decision).
 * - Persistence: localStorage key `openpine.locale`. The store
 *   (`useLocaleStore`) writes here on every change so the choice
 *   survives reloads. If no key is set on first load, the default
 *   is used and NOT persisted (so a user who never opens Settings
 *   gets the default cleanly).
 * - Switching is reactive: vue-i18n's `t()` re-evaluates on locale
 *   change, so every component using `$t` / `t` updates instantly
 *   without remounting.
 * - The set of supported locales is derived from `./locales/*.json`
 *   at build time (Vite's `import.meta.glob`); the store and
 *   LanguageSwitcher consume `SUPPORTED_LOCALES`.
 */

import { createI18n } from 'vue-i18n'
import en from './locales/en.json'
import ru from './locales/ru.json'

export const SUPPORTED_LOCALES = ['en', 'ru'] as const
export type LocaleCode = (typeof SUPPORTED_LOCALES)[number]

const LOCALE_STORAGE_KEY = 'openpine.locale'

function readPersistedLocale(): LocaleCode | null {
  try {
    const v = localStorage.getItem(LOCALE_STORAGE_KEY)
    if (v && (SUPPORTED_LOCALES as readonly string[]).includes(v)) {
      return v as LocaleCode
    }
  } catch {
    // localStorage may be unavailable (private mode, SSR, etc.)
  }
  return null
}

function detectInitialLocale(): LocaleCode {
  // 1) persisted choice
  const persisted = readPersistedLocale()
  if (persisted) return persisted
  // 2) browser preference
  try {
    const browser = navigator.language.toLowerCase().slice(0, 2)
    if ((SUPPORTED_LOCALES as readonly string[]).includes(browser)) {
      return browser as LocaleCode
    }
  } catch {
    // navigator may be undefined in tests
  }
  // 3) default
  return 'en'
}

export const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale: detectInitialLocale(),
  fallbackLocale: 'en',
  messages: { en, ru },
})

/** Imperative locale switcher used by the store. */
export function setLocale(code: LocaleCode): void {
  i18n.global.locale.value = code
  try {
    localStorage.setItem(LOCALE_STORAGE_KEY, code)
  } catch {
    // ignore
  }
  // Reflect in <html lang> for screen readers and CSS.
  try {
    document.documentElement.lang = code
  } catch {
    // ignore
  }
}

export function getCurrentLocale(): LocaleCode {
  return i18n.global.locale.value as LocaleCode
}

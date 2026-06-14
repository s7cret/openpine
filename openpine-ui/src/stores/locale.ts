import { defineStore } from 'pinia'
import { ref } from 'vue'
import { setLocale, getCurrentLocale, SUPPORTED_LOCALES, type LocaleCode } from '@/i18n'

/**
 * Locale store. The current locale lives in vue-i18n's global
 * state (single source of truth); this store exposes a reactive
 * mirror so components and the LanguageSwitcher can read/write
 * the choice with a familiar Pinia API.
 */
export const useLocaleStore = defineStore('locale', () => {
  const current = ref<LocaleCode>(getCurrentLocale())

  function change(code: LocaleCode) {
    if (!(SUPPORTED_LOCALES as readonly string[]).includes(code)) return
    setLocale(code)
    current.value = code
  }

  return { current, change, supported: SUPPORTED_LOCALES }
})

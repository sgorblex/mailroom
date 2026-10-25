import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import enCommon from '@/locales/en/common.json'
import itCommon from '@/locales/it/common.json'

export const SUPPORTED_LANGUAGES = ['en', 'it'] as const

/** English is the source language; Italian follows the browser when it asks for it. */
export function resolveLanguage(preferred: readonly string[]): string {
  for (const tag of preferred) {
    const base = tag.toLowerCase().split('-')[0]
    if (base !== undefined && (SUPPORTED_LANGUAGES as readonly string[]).includes(base)) {
      return base
    }
  }
  return 'en'
}

export const i18nReady: Promise<unknown> = i18n.use(initReactI18next).init({
  lng: resolveLanguage(typeof navigator === 'undefined' ? [] : navigator.languages),
  fallbackLng: 'en',
  supportedLngs: [...SUPPORTED_LANGUAGES],
  ns: ['common'],
  defaultNS: 'common',
  // A feature's namespace (added when its screens load) still reaches the shared strings.
  fallbackNS: 'common',
  resources: {
    en: { common: enCommon },
    it: { common: itCommon },
  },
  interpolation: {
    // React escapes on render.
    escapeValue: false,
  },
})

i18n.on('languageChanged', (language: string) => {
  document.documentElement.lang = language
})

export default i18n

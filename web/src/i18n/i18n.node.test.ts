import { describe, expect, it } from 'vitest'

import { resolveLanguage } from './i18n.ts'

describe('resolveLanguage', () => {
  it('picks the first supported browser language', () => {
    expect(resolveLanguage(['fr-FR', 'it-IT', 'en'])).toBe('it')
  })

  it('falls back to English', () => {
    expect(resolveLanguage(['de-DE'])).toBe('en')
    expect(resolveLanguage([])).toBe('en')
  })
})

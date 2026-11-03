import { describe, expect, it } from 'vitest'

import { FOLDER_ROLES } from '@/api/mail.ts'
import en from '@/locales/en/mail.json'
import it_ from '@/locales/it/mail.json'

import { formatListDate, formatSize } from './format.ts'

describe.each([
  ['en', en],
  ['it', it_],
])('the %s mail strings', (_language, locale) => {
  it('name every folder role', () => {
    expect(FOLDER_ROLES.filter((role) => !(role in locale.roles))).toEqual([])
  })
})

describe('format', () => {
  it('keeps sizes short', () => {
    expect(formatSize('en', 512)).toBe('512 byte')
    expect(formatSize('en', 40211)).toBe('39.3 kB')
  })

  it('shows the time for today and the date otherwise', () => {
    // Local times: "today" is the runner's calendar day, whatever its time zone.
    const now = new Date(2026, 9, 30, 12, 0)
    expect(formatListDate('en', new Date(2026, 9, 30, 9, 12).toISOString(), now)).toMatch(/\d:\d\d/)
    expect(formatListDate('en', new Date(2026, 0, 2, 9, 12).toISOString(), now)).toContain('2026')
    expect(formatListDate('en', null, now)).toBe('')
    expect(formatListDate('en', 'not a date', now)).toBe('not a date')
  })
})

import type { TFunction } from 'i18next'
import { describe, expect, it } from 'vitest'

import { FOLDER_ROLES } from '@/api/mail.ts'
import enCommon from '@/locales/en/common.json'
import en from '@/locales/en/mail.json'
import itCommon from '@/locales/it/common.json'
import it_ from '@/locales/it/mail.json'

import { formatListDate, formatSize } from './format.ts'
import { sentMessage } from './messages.ts'

describe.each([
  ['en', en],
  ['it', it_],
])('the %s mail strings', (_language, locale) => {
  it('name every folder role', () => {
    expect(FOLDER_ROLES.filter((role) => !(role in locale.roles))).toEqual([])
  })
})

/** Every key path of a strings file, with each plural form as itself: the two languages' forms match. */
function keys(strings: object, prefix = ''): string[] {
  return Object.entries(strings).flatMap(([key, value]: [string, unknown]) =>
    typeof value === 'object' && value !== null ? keys(value, `${prefix}${key}.`) : [`${prefix}${key}`],
  )
}

describe('the compose strings', () => {
  it('exist in both languages', () => {
    expect(keys(it_.compose).sort()).toEqual(keys(en.compose).sort())
    expect(keys(itCommon.mailErrors).sort()).toEqual(keys(enCommon.mailErrors).sort())
  })
})

describe.each([
  ['en', en],
  ['it', it_],
])('a delivered send in %s', (_language, locale) => {
  const t = ((key: string) => locale.compose[key.replace('compose.', '') as keyof typeof locale.compose]) as TFunction

  it.each(['draft_not_removed', 'draft_kept', 'original_not_marked'])('words the note %s', (code) => {
    const worded = sentMessage(t, { filed: true, rejected: [], note_codes: [code] })
    expect(worded.length).toBeGreaterThan(locale.compose.sent.length + 10)
    expect(worded).not.toContain('undefined')
  })

  it('leaves out a note it has no words for: the server text is never shown', () => {
    expect(sentMessage(t, { filed: true, rejected: [], note_codes: ['something_new'] })).toBe(locale.compose.sent)
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

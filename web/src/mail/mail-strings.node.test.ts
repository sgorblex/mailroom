import type { TFunction } from 'i18next'
import { describe, expect, it } from 'vitest'

import { FOLDER_ROLES } from '@/api/mail.ts'
import i18n from '@/i18n/i18n.ts'
import enCommon from '@/locales/en/common.json'
import en from '@/locales/en/mail.json'
import itCommon from '@/locales/it/common.json'
import it_ from '@/locales/it/mail.json'
import { keys } from '@/test/strings.ts'

import { formatListDate, formatSize } from './format.ts'
import { MAIL_NS } from './i18n.ts'
import { sentMessage } from './messages.ts'

describe.each([
  ['en', en],
  ['it', it_],
])('the %s mail strings', (_language, locale) => {
  it('name every folder role', () => {
    expect(FOLDER_ROLES.filter((role) => !(role in locale.roles))).toEqual([])
  })
})

describe('the compose strings', () => {
  it('exist in both languages', () => {
    expect(keys(it_.compose).sort()).toEqual(keys(en.compose).sort())
    expect(keys(itCommon.mailErrors).sort()).toEqual(keys(enCommon.mailErrors).sort())
  })
})

describe('the message strings', () => {
  it('exist in both languages', () => {
    expect(keys(it_.message).sort()).toEqual(keys(en.message).sort())
    expect(keys(it_.mailingList).sort()).toEqual(keys(en.mailingList).sort())
    expect(keys(it_.mute).sort()).toEqual(keys(en.mute).sort())
  })
})

describe('the remote image count', () => {
  it.each([
    ['en', 1, '1 remote image is blocked, so the sender cannot tell you opened it.', '1 remote image is shown for this message.'],
    ['en', 4, '4 remote images are blocked, so the sender cannot tell you opened it.', '4 remote images are shown for this message.'],
    ['it', 1, "1 immagine remota è bloccata, così il mittente non sa che l'hai aperto.", '1 immagine remota è mostrata per questo messaggio.'],
    ['it', 4, "4 immagini remote sono bloccate, così il mittente non sa che l'hai aperto.", '4 immagini remote sono mostrate per questo messaggio.'],
  ])('is worded in %s for %i', (language, count, blocked, shown) => {
    const t = i18n.getFixedT(language, MAIL_NS)
    expect(t('message.remoteBlocked', { count })).toBe(blocked)
    expect(t('message.remoteShown', { count })).toBe(shown)
  })
})

describe.each([
  ['en', en],
  ['it', it_],
])('a delivered send in %s', (_language, locale) => {
  const t = ((key: string) => locale.compose[key.replace('compose.', '') as keyof typeof locale.compose]) as TFunction

  it.each(['draft_not_removed', 'draft_kept', 'original_not_marked', 'original_not_marked_forwarded'])('words the note %s', (code) => {
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
    expect(formatListDate('en', new Date(2026, 9, 30, 9, 12).toISOString(), undefined, now)).toMatch(/\d:\d\d/)
    expect(formatListDate('en', new Date(2026, 0, 2, 9, 12).toISOString(), undefined, now)).toContain('2026')
    expect(formatListDate('en', null, undefined, now)).toBe('')
    expect(formatListDate('en', 'not a date', undefined, now)).toBe('not a date')
  })

  it('counts today and writes the time in the given zone', () => {
    const now = new Date('2026-10-31T01:00:00Z')
    const lateYesterday = '2026-10-30T23:30:00Z'
    expect(formatListDate('en', lateYesterday, 'Asia/Tokyo', now)).toBe('8:30 AM')
    expect(formatListDate('en', lateYesterday, 'UTC', now)).toBe('Oct 30, 2026')
  })
})

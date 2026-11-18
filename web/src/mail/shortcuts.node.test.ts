import { describe, expect, it } from 'vitest'

import en from '@/locales/en/mail.json'
import it_ from '@/locales/it/mail.json'
import { keys } from '@/test/strings.ts'

import { MAIL_ACTIONS } from './action-registry.ts'
import { SHEET_GROUPS } from './shortcuts.ts'

const entries = SHEET_GROUPS.flatMap((group) => group.entries)

describe('the shortcut sheet', () => {
  it('lists every key of the registry, each once', () => {
    const keys = entries.map((entry) => entry.key)
    expect(new Set(keys).size).toBe(keys.length)
    const registered = MAIL_ACTIONS.flatMap((action) => (action.shortcut === undefined ? [] : [action.shortcut]))
    expect(keys).toEqual(expect.arrayContaining(registered))
    expect(keys).toEqual(expect.arrayContaining(['j', 'k', 'x', 'Escape', '?']))
  })

  it.each([
    ['en', en],
    ['it', it_],
  ])('is named in %s', (_language, locale) => {
    const labels = [
      ...SHEET_GROUPS.map((group) => group.title),
      ...entries.flatMap((entry) => (entry.keyName === undefined ? [entry.label] : [entry.label, entry.keyName])),
    ]
    expect(keys(locale)).toEqual(expect.arrayContaining(labels))
  })
})

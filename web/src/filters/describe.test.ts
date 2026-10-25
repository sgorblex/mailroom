import { describe, expect, it } from 'vitest'

import i18n from '@/i18n/i18n.ts'
import schema from '@/test/filter-schema.json'

import { describeSpec, type Wording } from './describe.ts'
import { readSpec } from './spec.ts'
import { readVocabulary } from './vocabulary.ts'
import { FILTERS_NS } from './i18n.ts'

const vocabulary = readVocabulary(schema)

function wording(language: string): Wording {
  return { t: i18n.getFixedT(language, FILTERS_NS), language, vocabulary }
}

function summary(spec: unknown, language = 'en'): string {
  return describeSpec(wording(language), readSpec(spec))
}

describe('describeSpec', () => {
  it('words a filter as a sentence', () => {
    expect(
      summary({
        criterion: { and: [{ type: 'older_than_days', args: { days: 1 } }, { not: { type: 'is_starred' } }] },
        action: { seq: [{ type: 'mark_as_read' }, { type: 'trash', args: { folder: ['Bin'] } }] },
      }),
    ).toBe('If older than 1 day and not starred, mark as read, then move to the trash (Bin)')
    const always = { criterion: { type: 'anything' }, action: { type: 'move_to', args: { folder: ['A', 'B'] } } }
    expect(summary(always)).toBe('For every message, move to A / B')
  })

  it('tells choices from typed text by the schema, not by their case', () => {
    expect(
      summary({
        criterion: {
          or: [
            { type: 'subject_contains', args: { substring: 'URGENT' } },
            { type: 'lacks_flag', args: { flag: 'SEEN' } },
          ],
        },
        action: { type: 'star' },
      }),
    ).toBe('If the subject contains “URGENT” or not read, star')
  })

  it('names the headers a recipient criterion also looks at, and formats dates', () => {
    expect(
      summary({
        criterion: {
          and: [
            { type: 'to_contains', args: { addr: 'me@', incl_cc: true } },
            { type: 'older_than', args: { cutoff_date: '2026-01-31' } },
          ],
        },
        action: { type: 'no_op' },
      }),
    ).toBe('If the recipient (or Cc) contains “me@” and dated before Jan 31, 2026, do nothing')
  })

  it('falls back to the type and its values for a type it has no phrase for', () => {
    expect(summary({ criterion: { type: 'smells_like', args: { what: 'spam' } }, action: { type: 'star' } })).toBe(
      'If smells_like: “spam”, star',
    )
  })

  it('words it in Italian too', () => {
    expect(
      summary(
        {
          criterion: { and: [{ type: 'from_contains', args: { addr: 'news@' } }, { not: { type: 'is_starred' } }] },
          action: { type: 'move_to', args: { folder: ['Archivio'] } },
        },
        'it',
      ),
    ).toBe('Se il mittente contiene “news@” e non ha la stella, sposta in Archivio')
  })
})

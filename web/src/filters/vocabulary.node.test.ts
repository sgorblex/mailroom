import { describe, expect, it } from 'vitest'

import schema from '@/test/filter-schema.json'

import { initialArg, readVocabulary, VocabularyError } from './vocabulary.ts'

describe('readVocabulary', () => {
  const vocabulary = readVocabulary(schema)
  const criterion = (type: string) => vocabulary.criteria.find((def) => def.type === type)
  const action = (type: string) => vocabulary.actions.find((def) => def.type === type)

  it('lists every leaf criterion and action, without the and/or/not/seq groups', () => {
    expect(vocabulary.criteria.map((def) => def.type)).toContain('older_than_days')
    expect(vocabulary.criteria.map((def) => def.type)).not.toContain('and')
    expect(vocabulary.actions.map((def) => def.type)).toEqual(
      expect.arrayContaining(['move_to', 'trash', 'mark_as_read', 'delete']),
    )
  })

  it('reads each argument into the field that edits it', () => {
    expect(criterion('to_contains')?.args).toEqual([
      { name: 'addr', required: true, field: { kind: 'text' } },
      { name: 'incl_cc', required: false, field: { kind: 'boolean' } },
      { name: 'incl_bcc', required: false, field: { kind: 'boolean' } },
    ])
    expect(criterion('has_flag')?.args).toEqual([
      { name: 'flag', required: true, field: { kind: 'choice', options: ['SEEN', 'FLAGGED', 'ANSWERED'] } },
    ])
    expect(criterion('older_than')?.args[0]?.field).toEqual({ kind: 'date' })
    expect(criterion('older_than_days')?.args[0]?.field).toEqual({ kind: 'integer', minimum: 0, maximum: 36500 })
    expect(criterion('is_read')?.args).toEqual([])
    expect(action('move_to')?.args[0]).toEqual({
      name: 'folder',
      required: true,
      field: { kind: 'folder', optional: false },
    })
    expect(action('trash')?.args[0]).toEqual({
      name: 'folder',
      required: false,
      field: { kind: 'folder', optional: true },
    })
  })

  it('refuses a schema without criteria or actions', () => {
    expect(() => readVocabulary({ $defs: {} })).toThrow(VocabularyError)
    expect(() => readVocabulary('nope')).toThrow(VocabularyError)
  })
})

describe('initialArg', () => {
  it('starts required values empty and leaves optional ones out', () => {
    const today = '2026-10-20'
    expect(initialArg({ name: 'addr', required: true, field: { kind: 'text' } }, today)).toBe('')
    expect(initialArg({ name: 'incl_cc', required: false, field: { kind: 'boolean' } }, today)).toBeUndefined()
    expect(initialArg({ name: 'cutoff_date', required: true, field: { kind: 'date' } }, today)).toBe(today)
    const folder = { name: 'folder', required: true, field: { kind: 'folder', optional: false } } as const
    expect(initialArg(folder, today)).toEqual([])
  })
})

describe('readVocabulary on shapes it does not know', () => {
  const leafSchema = (args: Record<string, unknown>, type: Record<string, unknown> = { enum: ['x'] }) => ({
    $defs: {
      path: { type: 'array', items: { type: 'string' } },
      criterion: { type: 'object', properties: { type, args: { type: 'object', properties: args } } },
      action: { type: 'object', properties: { type: { const: 'act' } } },
    },
  })

  it('takes a type given as a const', () => {
    expect(readVocabulary(leafSchema({})).actions).toEqual([{ type: 'act', args: [] }])
    expect(readVocabulary(leafSchema({}, { const: 'only' })).criteria.map((def) => def.type)).toEqual(['only'])
  })

  it('edits only the folder path as a folder; another list or a union is left to the JSON editor', () => {
    const [def] = readVocabulary(
      leafSchema({
        folder: { $ref: '#/$defs/path' },
        tags: { type: 'array', items: { type: 'string' } },
        either: { anyOf: [{ type: 'string' }, { type: 'integer' }] },
        size: { type: 'number' },
      }),
    ).criteria
    expect(def?.args.map((arg) => [arg.name, arg.field.kind])).toEqual([
      ['folder', 'folder'],
      ['tags', 'unknown'],
      ['either', 'unknown'],
      ['size', 'unknown'],
    ])
  })
})

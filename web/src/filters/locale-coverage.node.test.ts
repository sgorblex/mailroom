import { describe, expect, it } from 'vitest'

import en from '@/locales/en/filters.json'
import it_ from '@/locales/it/filters.json'
import schema from '@/test/filter-schema.json'

import { readVocabulary } from './vocabulary.ts'

const vocabulary = readVocabulary(schema)
const leaves = [...vocabulary.criteria, ...vocabulary.actions]

type Locale = typeof en

function phraseKeys(locale: Locale): Set<string> {
  return new Set(Object.keys(locale.filterPhrases).map((key) => key.replace(/_(one|other)$/, '')))
}

const choices = leaves.flatMap((leaf) =>
  leaf.args.flatMap((arg) => (arg.field.kind === 'choice' ? arg.field.options : [])),
)

describe.each([
  ['en', en],
  ['it', it_],
])('the %s filter strings', (_language, locale) => {
  it('name and phrase every criterion and action in the schema', () => {
    const phrases = phraseKeys(locale)
    expect(leaves.filter((leaf) => !(leaf.type in locale.filterTypes)).map((leaf) => leaf.type)).toEqual([])
    expect(leaves.filter((leaf) => !phrases.has(leaf.type)).map((leaf) => leaf.type)).toEqual([])
  })

  it('name every argument and choice in the schema', () => {
    const args = leaves.flatMap((leaf) => leaf.args.map((arg) => arg.name))
    expect(args.filter((name) => !(name in locale.filterArgs))).toEqual([])
    expect(choices.filter((option) => !(option in locale.filterChoices))).toEqual([])
    expect(choices.filter((option) => !(option in locale.filterPhraseChoices))).toEqual([])
  })
})

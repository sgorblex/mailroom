import type { TFunction } from 'i18next'

import type { Args, CriterionNode, Leaf, SpecModel } from './spec.ts'
import type { ArgField, LeafDef, Vocabulary } from './vocabulary.ts'

/** What wording a spec needs: the reader's strings and language, and the schema's leaves. */
export interface Wording {
  t: TFunction
  language: string
  vocabulary: Vocabulary
}

/** A leaf type's name for people; a type the locale does not know shows as it is. */
export function typeLabel(t: TFunction, type: string): string {
  return t(`filterTypes.${type}`, { defaultValue: type })
}

export function argLabel(t: TFunction, name: string): string {
  return t(`filterArgs.${name}`, { defaultValue: name })
}

export function choiceLabel(t: TFunction, value: string): string {
  return t(`filterChoices.${value}`, { defaultValue: value })
}

/** A folder path for people: its segments joined. */
export function folderLabel(path: readonly string[]): string {
  return path.join(' / ')
}

/** A `YYYY-MM-DD` date in the reader's language; anything else as it is. */
function formatDate(language: string, value: string): string {
  const date = new Date(`${value}T00:00:00Z`)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(date.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeZone: 'UTC' }).format(date)
}

/** One argument's value for a phrase: a choice by its name, a folder by its path, a date formatted. */
function argValue(wording: Wording, field: ArgField | undefined, value: unknown): string | undefined {
  const { t, language } = wording
  if (typeof value === 'string') {
    if (field?.kind === 'choice') {
      return t(`filterPhraseChoices.${value}`, { defaultValue: choiceLabel(t, value) })
    }
    return field?.kind === 'date' ? formatDate(language, value) : value
  }
  if (typeof value === 'number') {
    return String(value)
  }
  if (Array.isArray(value)) {
    return value.length === 0 ? undefined : folderLabel(value.map(String))
  }
  return undefined
}

function leafDef(defs: LeafDef[], type: string): LeafDef | undefined {
  return defs.find((def) => def.type === type)
}

/** `To`'s "or Cc" and "or Bcc", for the leaves that take `incl_cc` and `incl_bcc`. */
function alsoHeaders(t: TFunction, args: Args): string {
  const cc = args['incl_cc'] === true
  const bcc = args['incl_bcc'] === true
  if (cc && bcc) {
    return t('filterPhraseParts.alsoCcBcc')
  }
  if (cc) {
    return t('filterPhraseParts.alsoCc')
  }
  return bcc ? t('filterPhraseParts.alsoBcc') : ''
}

/** The way a type the locale has no phrase for reads: its name and its values. */
function fallbackLeaf(wording: Wording, def: LeafDef | undefined, leaf: Leaf): string {
  const { t } = wording
  const values = Object.entries(leaf.args).flatMap(([name, value]) => {
    const field = def?.args.find((arg) => arg.name === name)?.field
    if (value === true) {
      return [argLabel(t, name).toLowerCase()]
    }
    const shown = argValue(wording, field, value)
    if (shown === undefined) {
      return []
    }
    const typed = typeof value === 'string' && field?.kind !== 'choice'
    return [typed ? t('filterPhraseParts.quoted', { value: shown }) : shown]
  })
  const label = typeLabel(t, leaf.type)
  return values.length === 0 ? label : `${label}: ${values.join(', ')}`
}

function describeLeaf(wording: Wording, defs: LeafDef[], leaf: Leaf): string {
  const { t } = wording
  const def = leafDef(defs, leaf.type)
  const values: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(leaf.args)) {
    values[name] = argValue(wording, def?.args.find((arg) => arg.name === name)?.field, value)
  }
  const count = Object.values(leaf.args).find((value) => typeof value === 'number')
  // The trash action names its folder only when it has one.
  const key = leaf.type === 'trash' && values['folder'] !== undefined ? 'trash_to' : leaf.type
  const phrase = t(`filterPhrases.${key}`, {
    ...values,
    also: alsoHeaders(t, leaf.args),
    ...(count === undefined ? {} : { count }),
    defaultValue: '',
  })
  return phrase === '' ? fallbackLeaf(wording, def, leaf) : phrase
}

export function describeCriterion(wording: Wording, node: CriterionNode, nested = false): string {
  const { t } = wording
  switch (node.kind) {
    case 'leaf':
      return describeLeaf(wording, wording.vocabulary.criteria, node)
    case 'not':
      return t('filters.summary.not', { criterion: describeCriterion(wording, node.child, true) })
    case 'and':
    case 'or': {
      const joined = node.children
        .map((child) => describeCriterion(wording, child, true))
        .join(` ${t(`filters.summary.${node.kind}`)} `)
      return nested && node.children.length > 1 ? `(${joined})` : joined
    }
  }
}

export function describeActions(wording: Wording, actions: Leaf[]): string {
  return actions
    .map((action) => describeLeaf(wording, wording.vocabulary.actions, action))
    .join(wording.t('filters.summary.then'))
}

/** A filter in a sentence: "If the sender contains “news@”, move to Archive". */
export function describeSpec(wording: Wording, model: SpecModel): string {
  const action = describeActions(wording, model.actions)
  if (model.criterion.kind === 'leaf' && model.criterion.type === 'anything') {
    return wording.t('filters.summary.always', { action })
  }
  return wording.t('filters.summary.rule', { criterion: describeCriterion(wording, model.criterion), action })
}

/** When a rule set runs, for people. */
export function triggerSummary(
  t: TFunction,
  ruleSet: { trigger: string; interval_minutes?: number | null },
): string {
  if (ruleSet.trigger === 'scheduled') {
    return t('filters.triggerSummary.scheduled', { count: ruleSet.interval_minutes ?? 0 })
  }
  return t(`filters.triggerSummary.${ruleSet.trigger}`, { defaultValue: ruleSet.trigger })
}

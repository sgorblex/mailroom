import { describe, expect, it } from 'vitest'

import schema from '@/test/filter-schema.json'

import {
  changeKind,
  missingArgs,
  newSpecModel,
  readSpec,
  SpecShapeError,
  writeSpec,
  type CriterionNode,
} from './spec.ts'
import { readVocabulary } from './vocabulary.ts'

const vocabulary = readVocabulary(schema)

const NERDS = {
  criterion: {
    and: [{ type: 'from_is', args: { addr: 'list@nerds.edu' } }, { not: { type: 'is_read' } }],
  },
  action: { seq: [{ type: 'mark_as_read' }, { type: 'move_to', args: { folder: ['Lists', 'Nerds'] } }] },
}

describe('readSpec and writeSpec', () => {
  it('round-trips a spec', () => {
    expect(writeSpec(readSpec(NERDS))).toEqual(NERDS)
  })

  it('writes one action as a leaf and flattens nested sequences', () => {
    const one = { criterion: { type: 'anything' }, action: { type: 'trash' } }
    expect(writeSpec(readSpec(one))).toEqual(one)
    const nested = {
      criterion: { type: 'anything' },
      action: { seq: [{ seq: [{ type: 'star' }, { type: 'mark_as_read' }] }, { type: 'no_op' }] },
    }
    expect(writeSpec(readSpec(nested))['action']).toEqual({
      seq: [{ type: 'star' }, { type: 'mark_as_read' }, { type: 'no_op' }],
    })
  })

  it('names where a spec is not one', () => {
    expect(() => readSpec({ criterion: { and: [] }, action: { type: 'star' } })).toThrow(
      expect.objectContaining({ where: 'criterion', problem: 'emptyList', group: 'and' }),
    )
    expect(() => readSpec({ criterion: { or: [{ nope: 1 }] }, action: { type: 'star' } })).toThrow(
      expect.objectContaining({ where: 'criterion.or[0]', problem: 'noType' }),
    )
    expect(() => readSpec([])).toThrow(SpecShapeError)
  })
})

describe('changeKind', () => {
  const leaf = readSpec({ criterion: { type: 'from_contains', args: { addr: 'a@b' } }, action: { type: 'star' } })
    .criterion

  it('keeps the arguments two leaf types share', () => {
    expect(writeSpec({ criterion: changeKind(vocabulary, leaf, 'leaf:from_is'), actions: [] })['criterion']).toEqual({
      type: 'from_is',
      args: { addr: 'a@b' },
    })
  })

  it('wraps a leaf into a group or a not, and swaps and for or', () => {
    const { key, ...content } = leaf
    const group = changeKind(vocabulary, leaf, 'and')
    expect(group).toMatchObject({ key, kind: 'and', children: [content] })
    expect(changeKind(vocabulary, group, 'or')).toMatchObject({ key, kind: 'or', children: [content] })
    const negated: CriterionNode = changeKind(vocabulary, leaf, 'not')
    expect(negated).toMatchObject({ key, kind: 'not', child: content })
    expect(changeKind(vocabulary, negated, 'or')).toMatchObject({ kind: 'or', children: [content] })
  })
})

describe('missingArgs', () => {
  it('flags a new filter until its address and folder are set', () => {
    const model = newSpecModel(vocabulary)
    expect(missingArgs(model, vocabulary)).toBe(true)
    const [action] = model.actions
    const filled = {
      criterion: { ...model.criterion, args: { addr: 'news@example.org' } } as CriterionNode,
      actions: action === undefined ? [] : [{ ...action, args: { folder: ['News'] } }],
    }
    expect(missingArgs(filled, vocabulary)).toBe(false)
  })
})

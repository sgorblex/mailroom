import { describe, expect, it } from 'vitest'

import { composeKey, sourceFromParam } from './compose-session.ts'

describe('the compose param', () => {
  it('brings back a composer the web client started', () => {
    const id = '0f8fad5b-d9cb-469f-a165-70867728950e'
    expect(sourceFromParam(composeKey(id, 1), 2)).toEqual({ kind: 'recover', account: 1, composeId: id })
  })

  it('brings back a draft an agent started: its compose id is no dashed UUID', () => {
    const id = '0f8fad5bd9cb469fa16570867728950e'
    expect(sourceFromParam(composeKey(id, 12), 2)).toEqual({ kind: 'recover', account: 12, composeId: id })
  })

  it('reads a ref, which holds no dot, as a draft to open', () => {
    expect(sourceFromParam('WzIsMSwiRHJhZnRzIiwiMSIsNDBd', 1)).toEqual({ kind: 'draft', ref: 'WzIsMSwiRHJhZnRzIiwiMSIsNDBd' })
  })

  it('reads a key whose account is no id, as an account named before ids were numbers, as a draft to open', () => {
    expect(sourceFromParam('abc.work', 1)).toEqual({ kind: 'draft', ref: 'abc.work' })
  })

  it('starts a new message', () => {
    expect(sourceFromParam('new', 1)).toEqual({ kind: 'new', account: 1 })
  })
})

import { describe, expect, it } from 'vitest'

import { composeKey, sourceFromParam } from './compose-session.ts'

describe('the compose param', () => {
  it('brings back a composer the web client started', () => {
    const id = '0f8fad5b-d9cb-469f-a165-70867728950e'
    expect(sourceFromParam(composeKey(id, 'work'), 'home')).toEqual({ kind: 'recover', account: 'work', composeId: id })
  })

  it('brings back a draft an agent started: its compose id is no dashed UUID', () => {
    const id = '0f8fad5bd9cb469fa16570867728950e'
    expect(sourceFromParam(composeKey(id, 'work'), 'home')).toEqual({ kind: 'recover', account: 'work', composeId: id })
  })

  it('takes everything after the compose id as the account', () => {
    expect(sourceFromParam('abc.me.example.org', 'home')).toEqual({ kind: 'recover', account: 'me.example.org', composeId: 'abc' })
  })

  it('reads a ref, which holds no dot, as a draft to open', () => {
    expect(sourceFromParam('WzEsIndvcmsiLCJEcmFmdHMiLCIxIiw0MF0', 'work')).toEqual({ kind: 'draft', ref: 'WzEsIndvcmsiLCJEcmFmdHMiLCIxIiw0MF0' })
  })

  it('starts a new message', () => {
    expect(sourceFromParam('new', 'work')).toEqual({ kind: 'new', account: 'work' })
  })
})

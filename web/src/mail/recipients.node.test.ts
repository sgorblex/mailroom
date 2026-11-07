import { describe, expect, it } from 'vitest'

import { isBlank, reconcileTray, trayPart, type ComposeForm, type TrayItem } from './compose-form.ts'
import { commitTyped, isDeliverable, parseRecipients, withRecipients } from './recipients.ts'

describe('recipients', () => {
  it('tells a deliverable address the way the server does', () => {
    expect(isDeliverable('ada@example.org')).toBe(true)
    expect(isDeliverable('ada@xn--bcher-kva.example')).toBe(true)
    expect(isDeliverable('ada')).toBe(false)
    expect(isDeliverable('ada@')).toBe(false)
    expect(isDeliverable('a@b@c')).toBe(false)
    expect(isDeliverable('adà@example.org')).toBe(false)
    expect(isDeliverable('ada@bücher.example')).toBe(false)
    expect(isDeliverable('a da@example.org')).toBe(false)
  })

  it('reads names, quotes and several addresses at once', () => {
    expect(parseRecipients('"Lovelace, Ada" <ada@x.org>; Bob <bob@x.org>, carol@x.org').addresses).toEqual([
      { name: 'Lovelace, Ada', address: 'ada@x.org' },
      { name: 'Bob', address: 'bob@x.org' },
      { name: '', address: 'carol@x.org' },
    ])
  })

  it('keeps what is not an address apart', () => {
    expect(parseRecipients('ada@x.org, half').invalid).toEqual(['half'])
  })

  it('adds each address once, ignoring case', () => {
    const { list } = withRecipients([{ name: '', address: 'ada@x.org' }], [{ name: 'Ada', address: 'ADA@x.org' }])
    expect(list).toHaveLength(1)
  })

  it('leaves in the field what cannot be committed, past the limit too', () => {
    const full = Array.from({ length: 50 }, (_, index) => ({ name: '', address: `a${String(index)}@x.org` }))
    expect(commitTyped(full, 'more@x.org')).toEqual({ list: full, text: 'more@x.org', invalid: true })
    expect(commitTyped([], 'half')).toEqual({ list: [], text: 'half', invalid: true })
    expect(commitTyped([], '')).toEqual({ list: [], text: '', invalid: false })
  })
})

describe('the attachment tray after a save', () => {
  const file = (name: string): TrayItem => ({ kind: 'file', id: name, file: new File(['x'], name) })
  const part = (name: string, number: string, ref = 'old'): TrayItem =>
    trayPart({ ref, part: number, name, type: 'text/plain', size: 1 })
  const saved = (name: string, number: string) => ({ part: number, name, type: 'text/plain', size: 1 })

  it('turns everything saved into the new version\'s parts', () => {
    const tray = [part('a.txt', '2'), file('b.txt')]
    expect(reconcileTray(tray, tray, 'new', [saved('a.txt', '2'), saved('b.txt', '3')])).toEqual([
      part('a.txt', '2', 'new'),
      part('b.txt', '3', 'new'),
    ])
  })

  it('keeps what the user added during the save, unsaved', () => {
    const before = [file('a.txt')]
    const added = file('b.txt')
    expect(reconcileTray([...before, added], before, 'new', [saved('a.txt', '2')])).toEqual([part('a.txt', '2', 'new'), added])
  })

  it('does not bring back what the user removed during the save', () => {
    const kept = file('b.txt')
    const before = [file('a.txt'), kept]
    const after = reconcileTray([kept], before, 'new', [saved('a.txt', '2'), saved('b.txt', '3')])
    expect(after).toEqual([part('b.txt', '3', 'new')])
  })
})

describe('a blank form', () => {
  const blank: ComposeForm = { from: null, to: [], cc: [], bcc: [], subject: '', text: '', tray: [] }

  it('has nothing typed or attached', () => {
    expect(isBlank(blank)).toBe(true)
    expect(isBlank({ ...blank, from: 'me@x.org' })).toBe(true)
    expect(isBlank({ ...blank, text: 'x' })).toBe(false)
  })
})

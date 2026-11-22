import { describe, expect, it } from 'vitest'

import { fromAddress, newMessageHtml, signatureOf, swapTextSignature } from './signature.ts'

const ME = { address: 'Me@work.example', html: '<p>Me</p>', text: 'Me' }
const ALIAS = { address: 'alias@work.example', html: '<p>Alias $&</p>', text: 'Alias $&' }
const BARE = { address: 'bare@work.example', html: '', text: '' }

describe('a signature', () => {
  it('is the identity\'s, by address in any case, none when empty', () => {
    expect(signatureOf([ME, ALIAS, BARE], 'me@WORK.example')).toBe(ME)
    expect(signatureOf([ME, ALIAS, BARE], 'bare@work.example')).toBeNull()
    expect(signatureOf([ME], null)).toBeNull()
  })

  it('goes from the default identity unless From names one', () => {
    const account = { identities: [{ address: 'a@x.org' }, { address: 'b@x.org', default: true }] } as Parameters<typeof fromAddress>[0]
    expect(fromAddress(account, null)).toBe('b@x.org')
    expect(fromAddress(account, 'a@x.org')).toBe('a@x.org')
  })

  it('starts a new message in its marked block, below room to write', () => {
    expect(newMessageHtml(ME)).toBe('<p></p><div data-mailroom-signature=""><p>Me</p></div>')
    expect(newMessageHtml(null)).toBe('')
  })

  it('is swapped in plain text only where it is as it was put', () => {
    const text = 'Hi\n\n-- \nMe\n\nOn Friday, Ana wrote:\n> Hello'
    expect(swapTextSignature(text, ME, ALIAS)).toBe('Hi\n\n-- \nAlias $&\n\nOn Friday, Ana wrote:\n> Hello')
    expect(swapTextSignature(text, ME, null)).toBe('Hi\n\nOn Friday, Ana wrote:\n> Hello')
    expect(swapTextSignature('Hi\n\n-- \nMe, edited', ME, ALIAS)).toBe('Hi\n\n-- \nMe, edited')
    expect(swapTextSignature('Hi', null, ALIAS)).toBe('Hi')
  })
})

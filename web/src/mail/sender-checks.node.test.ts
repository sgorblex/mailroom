import { describe, expect, it } from 'vitest'

import { authenticationState, recipientSummary, resultText } from './sender-checks.ts'

const checked = {
  verified: true,
  spf: null,
  spf_domain: null,
  dkim: null,
  dkim_domain: null,
  dmarc: null,
  dmarc_pass: false,
  from_domain: null,
}

describe('authenticationState', () => {
  it('believes nothing that is not verified, whatever the results say', () => {
    expect(authenticationState({ ...checked, verified: false, dmarc: 'pass', dmarc_pass: true })).toBe('unverified')
  })

  it('passes only on a DMARC pass for the one From', () => {
    expect(authenticationState({ ...checked, dmarc: 'pass', dmarc_pass: true, from_domain: 'example.org' })).toBe('pass')
    // A pass the server wrote for another From, or for two of them, is no pass.
    expect(authenticationState({ ...checked, spf: 'pass', dkim: 'pass', dmarc: 'pass' })).toBe('unconfirmed')
  })

  it('warns when a result failed', () => {
    expect(authenticationState({ ...checked, spf: 'pass', dkim: 'pass', dmarc: 'fail' })).toBe('fail')
    expect(authenticationState({ ...checked, spf: 'fail' })).toBe('fail')
    expect(authenticationState({ ...checked, spf: 'softfail', dkim: 'none' })).toBe('unconfirmed')
  })
})

describe('resultText', () => {
  it('names the domain a result was for, when the server gave one', () => {
    expect(resultText('pass', 'evil.example')).toBe('pass (evil.example)')
    expect(resultText('none', null)).toBe('none')
  })
})

describe('recipientSummary', () => {
  const people = (count: number, at = 'example.org') =>
    Array.from({ length: count }, (_, index) => ({ name: '', address: `p${String(index)}@${at}` }))

  it('shows a few recipients in full', () => {
    expect(recipientSummary(people(2), people(1, 'example.net'), 'p0@example.org')).toBeNull()
  })

  it('counts the others beside the identity it arrived at, in To or Cc, case aside', () => {
    expect(recipientSummary(people(3), [{ name: 'Me', address: 'Me@Example.org' }], 'me@example.org')).toEqual({
      you: true,
      others: 3,
    })
  })

  it('counts every recipient when the identity is not among them (a Bcc)', () => {
    expect(recipientSummary(people(4), [], 'me@example.org')).toEqual({ you: false, others: 4 })
    expect(recipientSummary(people(4), [], null)).toEqual({ you: false, others: 4 })
  })
})

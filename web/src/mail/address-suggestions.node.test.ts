import { describe, expect, it } from 'vitest'

import { matchesTyped } from './address-suggestions.ts'

describe('a suggestion kept while typing goes on', () => {
  const ada = { name: 'Ada Lovelace', address: 'ada@example.org', sent: true }

  it('matches as the server does: the address, the name or a word of it, by its start', () => {
    expect(matchesTyped(ada, 'ADA@')).toBe(true)
    expect(matchesTyped(ada, 'love')).toBe(true)
    expect(matchesTyped(ada, ' ada l ')).toBe(true)
    expect(matchesTyped(ada, 'lace')).toBe(false)
    expect(matchesTyped(ada, 'example')).toBe(false)
  })
})

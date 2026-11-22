import { describe, expect, it } from 'vitest'

import { linkTarget } from './rich-text.ts'

describe('a typed link', () => {
  it('goes to a web or mail address only', () => {
    expect(linkTarget(' https://example.org/a ')).toBe('https://example.org/a')
    expect(linkTarget('MAILTO:ada@example.org')).toBe('MAILTO:ada@example.org')
    expect(linkTarget('example.org/a')).toBe('https://example.org/a')
    expect(linkTarget('ada@example.org')).toBe('mailto:ada@example.org')
    expect(linkTarget('javascript:alert(1)')).toBeNull()
    expect(linkTarget('data:text/html,x')).toBeNull()
    expect(linkTarget('')).toBeNull()
  })
})

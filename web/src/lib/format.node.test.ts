import { describe, expect, it } from 'vitest'

import { formatDateTime } from './format.ts'

describe('formatDateTime', () => {
  it('writes the date and time in the given zone', () => {
    const at = '2026-10-30T23:30:00Z'
    expect(formatDateTime('en', at, 'UTC')).toBe('Oct 30, 2026, 11:30 PM')
    expect(formatDateTime('en', at, 'Europe/Rome')).toBe('Oct 31, 2026, 12:30 AM')
  })

  it('shows text that is no date as it is', () => {
    expect(formatDateTime('en', null, 'UTC')).toBe('')
    expect(formatDateTime('en', 'not a date', 'UTC')).toBe('not a date')
  })
})

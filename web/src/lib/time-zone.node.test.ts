import { describe, expect, it } from 'vitest'

import { writableTimeZone } from './time-zone.ts'

describe('writableTimeZone', () => {
  it('keeps a zone the browser knows', () => {
    expect(writableTimeZone('Asia/Tokyo')).toBe('Asia/Tokyo')
  })

  it("falls back to the browser's zone when none is saved or the browser does not know it", () => {
    expect(writableTimeZone(null)).toBeUndefined()
    expect(writableTimeZone('Not/AZone')).toBeUndefined()
  })
})

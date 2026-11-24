import { describe, expect, it } from 'vitest'

import { clamp } from './number.ts'

describe('clamp', () => {
  it('keeps a value within its bounds, the lower one winning', () => {
    expect(clamp(25, 20, 30)).toBe(25)
    expect(clamp(10, 20, 30)).toBe(20)
    expect(clamp(40, 20, 30)).toBe(30)
    expect(clamp(25, 20, 10)).toBe(20)
  })
})

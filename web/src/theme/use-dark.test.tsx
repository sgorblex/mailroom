import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { useDark } from './use-dark.ts'

afterEach(() => {
  document.documentElement.classList.remove('dark')
})

describe('useDark', () => {
  it('follows the dark class on <html>', async () => {
    const { result } = renderHook(() => useDark())
    expect(result.current).toBe(false)
    await act(async () => {
      document.documentElement.classList.add('dark')
      await Promise.resolve()
    })
    expect(result.current).toBe(true)
    await act(async () => {
      document.documentElement.classList.remove('dark')
      await Promise.resolve()
    })
    expect(result.current).toBe(false)
  })
})

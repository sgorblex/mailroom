import { act, renderHook } from '@testing-library/react'
import type { MouseEvent, PointerEvent } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LONG_PRESS_MS, useLongPress } from './long-press.ts'

const pointer = (pointerType: string, clientX = 0, clientY = 0): PointerEvent =>
  ({ pointerId: 1, pointerType, clientX, clientY }) as unknown as PointerEvent

function click(): MouseEvent & { stopped: boolean } {
  const event = {
    defaultPrevented: false,
    stopped: false,
    preventDefault() {
      event.defaultPrevented = true
    },
    stopPropagation() {
      event.stopped = true
    },
  }
  return event as unknown as MouseEvent & { stopped: boolean }
}

describe('useLongPress', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('runs after a finger is held still, and swallows the click the lifted finger sends', () => {
    const onLongPress = vi.fn()
    const { result } = renderHook(() => useLongPress())
    const handlers = result.current.bind(onLongPress)
    handlers.onPointerDown(pointer('touch'))
    expect(result.current.held()).toBe(true)
    act(() => {
      vi.advanceTimersByTime(LONG_PRESS_MS - 1)
    })
    expect(onLongPress).not.toHaveBeenCalled()
    handlers.onPointerMove(pointer('touch', 5, 5))
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(onLongPress).toHaveBeenCalledOnce()
    handlers.onPointerUp()
    expect(result.current.held()).toBe(true)
    const first = click()
    handlers.onClickCapture(first)
    expect(first.defaultPrevented).toBe(true)
    expect(first.stopped).toBe(true)
    // Only that one click.
    const second = click()
    handlers.onClickCapture(second)
    expect(second.defaultPrevented).toBe(false)
    expect(result.current.held()).toBe(false)
  })

  it('does nothing when the finger moves, lifts early or is a mouse', () => {
    const onLongPress = vi.fn()
    const { result } = renderHook(() => useLongPress())
    const handlers = result.current.bind(onLongPress)

    handlers.onPointerDown(pointer('touch'))
    handlers.onPointerMove(pointer('touch', 0, 20))
    handlers.onPointerDown(pointer('touch'))
    handlers.onPointerUp()
    handlers.onPointerDown(pointer('touch'))
    handlers.onPointerCancel()
    handlers.onPointerDown(pointer('mouse'))
    expect(result.current.held()).toBe(false)
    act(() => {
      vi.advanceTimersByTime(LONG_PRESS_MS * 2)
    })
    expect(onLongPress).not.toHaveBeenCalled()
    const event = click()
    handlers.onClickCapture(event)
    expect(event.defaultPrevented).toBe(false)
  })
})

import { useCallback, useEffect, useMemo, useRef, type MouseEvent, type PointerEvent } from 'react'

/** How long a finger stays down, still, before it is a long press. */
export const LONG_PRESS_MS = 500
/** How far a finger may move and still be held still. */
const SLOP = 10

interface Press {
  pointer: number
  x: number
  y: number
  timer: ReturnType<typeof setTimeout>
}

export interface LongPressHandlers {
  onPointerDown: (event: PointerEvent) => void
  onPointerMove: (event: PointerEvent) => void
  onPointerUp: () => void
  onPointerCancel: () => void
  onClickCapture: (event: MouseEvent) => void
}

export interface LongPress {
  /** The handlers for an element a finger held on runs `onLongPress`: one press at a time across all of them. */
  bind: (onLongPress: () => void) => LongPressHandlers
  /**
   * Whether a finger is down, or a long press just ran: the browser's own long press (a context
   * menu, a link's preview) should then do nothing.
   */
  held: () => boolean
}

/**
 * A finger held still on an element for `LONG_PRESS_MS` runs its callback; touch only, as a mouse
 * has its right click. The click the lifted finger may then send is the long press's own and
 * opens nothing.
 */
export function useLongPress(): LongPress {
  const press = useRef<Press | null>(null)
  const fired = useRef(false)

  const cancel = useCallback(() => {
    if (press.current !== null) {
      clearTimeout(press.current.timer)
      press.current = null
    }
  }, [])

  useEffect(() => cancel, [cancel])

  return useMemo<LongPress>(
    () => ({
      bind: (onLongPress) => ({
        onPointerDown: (event) => {
          cancel()
          fired.current = false
          if (event.pointerType !== 'touch') {
            return
          }
          press.current = {
            pointer: event.pointerId,
            x: event.clientX,
            y: event.clientY,
            timer: setTimeout(() => {
              press.current = null
              fired.current = true
              onLongPress()
            }, LONG_PRESS_MS),
          }
        },
        onPointerMove: (event) => {
          const held = press.current
          if (held !== null && held.pointer === event.pointerId
            && Math.hypot(event.clientX - held.x, event.clientY - held.y) > SLOP) {
            cancel()
          }
        },
        onPointerUp: cancel,
        onPointerCancel: cancel,
        onClickCapture: (event) => {
          if (fired.current) {
            fired.current = false
            event.preventDefault()
            event.stopPropagation()
          }
        },
      }),
      held: () => press.current !== null || fired.current,
    }),
    [cancel],
  )
}

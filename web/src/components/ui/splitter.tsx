import { useRef, type KeyboardEvent, type PointerEvent, type ReactElement } from 'react'

import { clamp } from '@/lib/number.ts'
import { cn } from '@/lib/utils'

/** How far an arrow key moves the divider, and Shift with it. */
const STEP = 16
const BIG_STEP = 64

/**
 * A divider between two panes that sets the size of the one before it (`controls`), in pixels:
 * dragged with a pointer, or moved with the arrow keys (Shift for bigger steps), Home and End;
 * a double-click gives `onReset`. A vertical line, for panes side by side. A drag reports each
 * size on the way (`onChange`) and the one it ends on once (`onCommit`), so only that is kept; a
 * key gives both at once.
 */
export function Splitter({
  value,
  min,
  max,
  onChange,
  onCommit,
  onReset,
  controls,
  label,
  valueText,
  className,
}: {
  value: number
  min: number
  max: number
  /** A new size, already within `min` and `max`, while it is being chosen. */
  onChange: (value: number) => void
  /** The size chosen: where a drag ends, or after a key. */
  onCommit: (value: number) => void
  onReset: () => void
  /** The id of the pane it sizes. */
  controls: string
  label: string
  /** The size in words, for a screen reader. */
  valueText?: string
  className?: string
}): ReactElement {
  // Where a drag started (the pointer's x and the size then), and the size it last reported.
  const drag = useRef<{ x: number; value: number; last: number | null } | null>(null)
  const change = (next: number): number | null => {
    const kept = clamp(Math.round(next), min, max)
    if (kept === value) {
      return null
    }
    onChange(kept)
    return kept
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    const step = event.shiftKey ? BIG_STEP : STEP
    const next =
      event.key === 'ArrowLeft'
        ? value - step
        : event.key === 'ArrowRight'
          ? value + step
          : event.key === 'Home'
            ? min
            : event.key === 'End'
              ? max
              : null
    if (next !== null) {
      event.preventDefault()
      const kept = change(next)
      if (kept !== null) {
        onCommit(kept)
      }
    }
  }

  function onPointerDown(event: PointerEvent<HTMLDivElement>): void {
    if (event.button !== 0) {
      return
    }
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    event.currentTarget.focus()
    drag.current = { x: event.clientX, value, last: null }
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>): void {
    if (drag.current !== null) {
      drag.current.last = change(drag.current.value + event.clientX - drag.current.x) ?? drag.current.last
    }
  }

  function onPointerEnd(): void {
    const last = drag.current?.last ?? null
    drag.current = null
    if (last !== null) {
      onCommit(last)
    }
  }

  // A focusable separator is a widget (WAI-ARIA's window splitter); jsx-a11y takes every separator
  // for a static one.
  /* eslint-disable jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/no-noninteractive-tabindex */
  return (
    <div
      role="separator"
      tabIndex={0}
      aria-orientation="vertical"
      aria-valuenow={value}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuetext={valueText}
      aria-controls={controls}
      aria-label={label}
      onKeyDown={onKeyDown}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerEnd}
      onPointerCancel={onPointerEnd}
      onDoubleClick={onReset}
      className={cn(
        // A thin line with a wider grip around it; the ring shows where focus is.
        'relative z-10 w-px shrink-0 cursor-col-resize touch-none bg-border outline-none select-none',
        'before:absolute before:inset-y-0 before:-right-1.5 before:-left-1.5 before:content-[""]',
        'hover:bg-primary/60 focus-visible:bg-primary focus-visible:ring-[3px] focus-visible:ring-ring/50',
        className,
      )}
    />
  )
  /* eslint-enable jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/no-noninteractive-tabindex */
}

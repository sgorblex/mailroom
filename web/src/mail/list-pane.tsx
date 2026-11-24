import { useLayoutEffect, useState, type CSSProperties, type ReactNode, type ReactElement, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'

import { useAppearance } from '@/auth/auth-context.ts'
import { Splitter } from '@/components/ui/splitter.tsx'
import { useDevicePref } from '@/lib/device-prefs.ts'
import { clamp } from '@/lib/number.ts'
import { cn } from '@/lib/utils.ts'

import { MAIL_NS } from './i18n.ts'
import { LIST_WIDTH, listHeightBounds, listWidthBounds, ROW_ESTIMATE } from './list-size.ts'
import type { ReadingPanePosition } from './reading-pane.ts'

const WIDTH_PREF = 'mail.listWidth'
const HEIGHT_PREF = 'mail.listHeight'
const isSize = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
/** A height this device chose, or `null`: none, the list taking its share of the room. */
const isHeight = (value: unknown): value is number | null => value === null || isSize(value)

/**
 * The room the list and the reading pane share, across (`width`) or down (`height`), measured
 * again as either changes size (the window or the folder pane); `null` until laid out. Measured
 * first before the first paint, so a stored size too large for the window never shows.
 */
function useSharedRoom(
  listed: HTMLElement | null,
  pane: RefObject<HTMLElement | null>,
  axis: 'width' | 'height' | null,
): number | null {
  const [room, setRoom] = useState<number | null>(null)
  useLayoutEffect(() => {
    const shown = pane.current
    if (axis === null || listed === null || shown === null || typeof ResizeObserver === 'undefined') {
      return
    }
    const measure = (): void => {
      const size =
        axis === 'width' ? listed.offsetWidth + shown.offsetWidth : listed.offsetHeight + shown.offsetHeight
      setRoom(size > 0 ? size : null)
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(listed)
    observer.observe(shown)
    return () => {
      observer.disconnect()
    }
  }, [listed, pane, axis])
  return room
}

/**
 * The message list's pane and, on a wide screen, the divider after it that sets its size: its
 * width with the reading pane (`pane`) on the right, its height with the pane below, this device's
 * choice each (the height, until one is chosen, a share of the room), kept within bounds and
 * leaving the pane room. The choice itself stands when the window shrinks, so the list grows back
 * with it; a drag is kept once, where it ends. With the pane `off` the list takes the room, and
 * stays laid out, scrolled where it was, under an open message (`overlaid`).
 */
export function ListPane({
  id,
  label,
  className,
  pane,
  position,
  overlaid,
  children,
}: {
  id: string
  label: string
  className: string
  /** The reading pane next to it. */
  pane: RefObject<HTMLElement | null>
  position: ReadingPanePosition
  /** An open message covers it: hidden, out of reach of focus, and kept as it was. */
  overlaid: boolean
  children: ReactNode
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [chosenWidth, setWidth] = useDevicePref(WIDTH_PREF, LIST_WIDTH, isSize)
  const [chosenHeight, setHeight] = useDevicePref<number | null>(HEIGHT_PREF, null, isHeight)
  const { density } = useAppearance().appearance
  // The size a drag is passing through, kept only when it ends.
  const [dragged, setDragged] = useState<number | null>(null)
  const [section, setSection] = useState<HTMLElement | null>(null)
  const below = position === 'below'
  const room = useSharedRoom(section, pane, position === 'off' ? null : below ? 'height' : 'width')
  const bounds = below ? listHeightBounds(room, ROW_ESTIMATE[density]) : listWidthBounds(room)
  const chosen = below ? (chosenHeight ?? bounds.initial) : chosenWidth
  const size = clamp(dragged ?? chosen, bounds.min, bounds.max)
  return (
    <>
      <section
        ref={setSection}
        id={id}
        aria-label={label}
        inert={overlaid}
        className={cn(
          position === 'right' && 'lg:w-(--list-size) lg:shrink-0',
          below && 'lg:h-(--list-size) lg:shrink-0',
          position === 'off' && 'lg:flex-1',
          overlaid && 'invisible',
          className,
        )}
        style={position === 'off' ? undefined : ({ '--list-size': `${String(size)}px` } as CSSProperties)}
      >
        {children}
      </section>
      {position !== 'off' && (
        <Splitter
          value={size}
          min={bounds.min}
          max={bounds.max}
          onChange={setDragged}
          onCommit={(value) => {
            setDragged(null)
            if (below) {
              setHeight(value)
            } else {
              setWidth(value)
            }
          }}
          onReset={() => {
            if (below) {
              setHeight(null)
            } else {
              setWidth(LIST_WIDTH)
            }
          }}
          controls={id}
          label={t('list.resize')}
          valueText={t(below ? 'list.height' : 'list.width', { px: size })}
          orientation={below ? 'horizontal' : 'vertical'}
          className="hidden lg:block"
        />
      )}
    </>
  )
}

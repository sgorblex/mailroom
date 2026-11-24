import { useLayoutEffect, useState, type CSSProperties, type ReactNode, type ReactElement, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'

import { Splitter } from '@/components/ui/splitter.tsx'
import { useDevicePref } from '@/lib/device-prefs.ts'
import { clamp } from '@/lib/number.ts'
import { cn } from '@/lib/utils.ts'

import { MAIL_NS } from './i18n.ts'

/** The message list's width on a wide screen, in pixels: by default, and the narrowest and widest. */
export const LIST_WIDTH = 480
export const LIST_MIN = 320
export const LIST_MAX = 960
/** What the reading pane keeps however wide the list is made: a readable measure, and room for its
 * action bar's first actions. */
export const READING_MIN = 480

const WIDTH_PREF = 'mail.listWidth'
const isWidth = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)

/**
 * The room the list and the reading pane share, measured again as either changes size (the window
 * or the folder pane); `null` until laid out. Measured first before the first paint, so a stored
 * width too wide for the window never shows.
 */
function useSharedRoom(listed: HTMLElement | null, pane: RefObject<HTMLElement | null>): number | null {
  const [room, setRoom] = useState<number | null>(null)
  useLayoutEffect(() => {
    const shown = pane.current
    if (listed === null || shown === null || typeof ResizeObserver === 'undefined') {
      return
    }
    const measure = (): void => {
      const width = listed.offsetWidth + shown.offsetWidth
      setRoom(width > 0 ? width : null)
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(listed)
    observer.observe(shown)
    return () => {
      observer.disconnect()
    }
  }, [listed, pane])
  return room
}

/**
 * The message list's pane and, on a wide screen, the divider after it that sets its width: this
 * device's choice, kept within bounds and leaving the reading pane (`pane`) room. The choice
 * itself stands when the window narrows, so the list widens back with it. A drag is kept once, where
 * it ends.
 */
export function ListPane({
  id,
  label,
  className,
  pane,
  children,
}: {
  id: string
  label: string
  className: string
  /** The reading pane next to it. */
  pane: RefObject<HTMLElement | null>
  children: ReactNode
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [chosen, setChosen] = useDevicePref(WIDTH_PREF, LIST_WIDTH, isWidth)
  // The width a drag is passing through, kept only when it ends.
  const [dragged, setDragged] = useState<number | null>(null)
  const [section, setSection] = useState<HTMLElement | null>(null)
  const room = useSharedRoom(section, pane)
  const max = room === null ? LIST_MAX : clamp(room - READING_MIN, LIST_MIN, LIST_MAX)
  const width = clamp(dragged ?? chosen, LIST_MIN, max)
  return (
    <>
      <section
        ref={setSection}
        id={id}
        aria-label={label}
        className={cn('lg:w-(--list-width) lg:shrink-0', className)}
        style={{ '--list-width': `${String(width)}px` } as CSSProperties}
      >
        {children}
      </section>
      <Splitter
        value={width}
        min={LIST_MIN}
        max={max}
        onChange={setDragged}
        onCommit={(value) => {
          setDragged(null)
          setChosen(value)
        }}
        onReset={() => {
          setChosen(LIST_WIDTH)
        }}
        controls={id}
        label={t('list.resize')}
        valueText={t('list.width', { px: width })}
        className="hidden lg:block"
      />
    </>
  )
}

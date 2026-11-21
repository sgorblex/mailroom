import { act } from '@testing-library/react'
import { useEffect, useReducer, useState } from 'react'
import type { VirtualItem } from '@tanstack/react-virtual'

/** Every row's height, as jsdom lays nothing out. */
const ROW = 80

/** How many rows the view shows. */
const VIEW = 10

interface Options {
  count: number
  getItemKey?: (index: number) => string | number
}

/**
 * Stands in for `@tanstack/react-virtual`'s virtualizer under jsdom (`setup.ts`), where nothing has
 * a size: every row is rendered, as with an endless overscan, so a test finds any row loaded, while
 * the view (`range`) holds `VIEW` rows from the top one. `scrollToIndex` and `scrollToOffset` move
 * it as a browser would; `scrollListToEnd` brings the last row into view.
 */
class FakeVirtualizer {
  options: Options = { count: 0 }
  top = 0
  readonly rerender: () => void

  constructor(rerender: () => void) {
    this.rerender = rerender
  }

  get measurementsCache(): VirtualItem[] {
    return Array.from({ length: this.options.count }, (_, index) => ({
      key: this.options.getItemKey?.(index) ?? index,
      index,
      start: index * ROW,
      end: (index + 1) * ROW,
      size: ROW,
      lane: 0,
    }))
  }

  get range(): { startIndex: number; endIndex: number } | null {
    return this.options.count === 0 ? null : { startIndex: this.top, endIndex: Math.min(this.options.count, this.top + VIEW) - 1 }
  }

  get scrollOffset(): number {
    return this.top * ROW
  }

  getVirtualItems = (): VirtualItem[] => this.measurementsCache

  getTotalSize = (): number => this.options.count * ROW

  getVirtualItemForOffset = (offset: number): VirtualItem | undefined => this.measurementsCache[Math.floor(offset / ROW)]

  /** Every row has its height already. */
  measureElement = (): undefined => undefined

  scrollToOffset = (offset: number): void => {
    this.moveTo(Math.floor(offset / ROW))
  }

  /** As `align: 'auto'`: only as far as brings the row into view. */
  scrollToIndex = (index: number): void => {
    if (index < this.top) {
      this.moveTo(index)
    } else if (index >= this.top + VIEW) {
      this.moveTo(index - VIEW + 1)
    }
  }

  setOptions(options: Options): void {
    this.options = options
  }

  moveTo(top: number): void {
    const next = Math.max(0, Math.min(top, this.options.count - VIEW))
    if (next !== this.top) {
      this.top = next
      this.rerender()
    }
  }
}

/** As the library's; never asked here, where every row is rendered. */
export function defaultRangeExtractor(range: { startIndex: number; endIndex: number; overscan: number; count: number }): number[] {
  const start = Math.max(range.startIndex - range.overscan, 0)
  const end = Math.min(range.endIndex + range.overscan, range.count - 1)
  return Array.from({ length: end - start + 1 }, (_, index) => start + index)
}

const mounted = new Set<FakeVirtualizer>()

export function useVirtualizer(options: Options): FakeVirtualizer {
  const [, rerender] = useReducer((count: number) => count + 1, 0)
  const [instance] = useState(() => new FakeVirtualizer(rerender))
  instance.setOptions(options)
  useEffect(() => {
    mounted.add(instance)
    return () => {
      mounted.delete(instance)
    }
  }, [instance])
  return instance
}

/** Scrolls every list shown to its last row, which reads its next page. */
export function scrollListToEnd(): void {
  act(() => {
    for (const instance of mounted) {
      instance.moveTo(instance.options.count)
    }
  })
}

/** The rows in view of the list shown. */
export function listView(): { startIndex: number; endIndex: number } | null {
  const [instance] = mounted
  return instance?.range ?? null
}

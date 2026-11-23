import { describe, expect, it } from 'vitest'
import type { Virtualizer } from '@tanstack/react-virtual'

import { measureRow } from './row-measure.ts'

/** A virtualizer as far as measuring a row goes: row 0 measured at 70 px once, row 1 never. */
function instance(): Virtualizer<Element, Element> {
  return {
    options: { horizontal: false, getItemKey: (index: number) => `row ${String(index)}`, estimateSize: () => 76 },
    itemSizeCache: new Map([['row 0', 70]]),
    indexFromElement: (element: Element) => (element as unknown as { index: number }).index,
  } as unknown as Virtualizer<Element, Element>
}

function row(index: number, offsetHeight: number): Element {
  return { index, offsetHeight } as unknown as Element
}

function entry(blockSize: number): ResizeObserverEntry {
  return { borderBoxSize: [{ blockSize, inlineSize: 300 }] } as unknown as ResizeObserverEntry
}

describe('measureRow', () => {
  it('takes the height the browser measures', () => {
    expect(measureRow(row(0, 0), entry(64), instance())).toBe(64)
    expect(measureRow(row(1, 80), undefined, instance())).toBe(80)
  })

  it('keeps the height a row had while the list is hidden, which lays it out at none', () => {
    expect(measureRow(row(0, 0), entry(0), instance())).toBe(70)
  })

  it('takes the estimate for a hidden row never measured', () => {
    expect(measureRow(row(1, 0), entry(0), instance())).toBe(76)
  })
})

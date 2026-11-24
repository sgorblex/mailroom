import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useRef, type ReactElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  LIST_CHROME_HEIGHT,
  LIST_MAX,
  LIST_MAX_HEIGHT,
  LIST_MIN,
  LIST_MIN_HEIGHT,
  LIST_MIN_ROWS,
  LIST_WIDTH,
  listHeightBounds,
  listWidthBounds,
  READING_MIN,
  READING_MIN_HEIGHT,
  ROW_ESTIMATE,
} from './list-size.ts'
import { ListPane } from './list-pane.tsx'
import type { ReadingPanePosition } from './reading-pane.ts'

/** A `ResizeObserver` the test fires by hand, as jsdom lays nothing out. */
const observers: (() => void)[] = []
class FakeResizeObserver {
  private readonly callback: () => void
  constructor(callback: () => void) {
    this.callback = callback
  }
  observe(): void {
    observers.push(this.callback)
  }
  disconnect(): void {
    for (let index = observers.indexOf(this.callback); index !== -1; index = observers.indexOf(this.callback)) {
      observers.splice(index, 1)
    }
  }
}

/** Lays the list and the reading pane out as sharing `room` pixels, across or down, and tells the observers. */
function layOut(room: number, axis: 'offsetWidth' | 'offsetHeight' = 'offsetWidth'): void {
  const list = screen.getByRole('region', { name: 'List' })
  const pane = screen.getByRole('region', { name: 'Pane' })
  Object.defineProperty(list, axis, { configurable: true, value: room / 2 })
  Object.defineProperty(pane, axis, { configurable: true, value: room / 2 })
  act(() => {
    for (const observer of [...observers]) {
      observer()
    }
  })
}

function Panes({ position = 'right' }: { position?: ReadingPanePosition }): ReactElement {
  const pane = useRef<HTMLElement>(null)
  return (
    <div>
      <ListPane id="list" label="List" className="flex" pane={pane} position={position} overlaid={false}>
        rows
      </ListPane>
      <section ref={pane} aria-label="Pane" />
    </div>
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  observers.length = 0
})

describe('the message list pane', () => {
  it('starts at the default width, which the divider sizes', () => {
    render(<Panes />)
    const splitter = screen.getByRole('separator', { name: 'Resize the message list' })
    expect(splitter).toHaveAttribute('aria-controls', 'list')
    expect(splitter).toHaveAttribute('aria-valuenow', String(LIST_WIDTH))
    expect(splitter).toHaveAttribute('aria-valuemin', String(LIST_MIN))
    expect(splitter).toHaveAttribute('aria-valuemax', String(LIST_MAX))
    expect(splitter).toHaveAttribute('aria-valuetext', `${String(LIST_WIDTH)} px wide`)
    expect(screen.getByRole('region', { name: 'List' }).style.getPropertyValue('--list-size')).toBe(`${String(LIST_WIDTH)}px`)
  })

  it("keeps this device's width, and a double-click forgets it", async () => {
    const user = userEvent.setup()
    const { unmount } = render(<Panes />)
    screen.getByRole('separator').focus()
    await user.keyboard('{ArrowRight}{ArrowRight}')
    expect(window.localStorage.getItem('mailroom.ui.mail.listWidth')).toBe(String(LIST_WIDTH + 32))
    unmount()
    render(<Panes />)
    const splitter = screen.getByRole('separator')
    expect(splitter).toHaveAttribute('aria-valuenow', String(LIST_WIDTH + 32))
    fireEvent.doubleClick(splitter)
    expect(splitter).toHaveAttribute('aria-valuenow', String(LIST_WIDTH))
  })

  it('keeps a drag once, where it ends', () => {
    render(<Panes />)
    const splitter = screen.getByRole('separator')
    // jsdom has no pointer capture.
    splitter.setPointerCapture = vi.fn()
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    fireEvent.pointerDown(splitter, { button: 0, clientX: 100, pointerId: 1 })
    for (const x of [110, 140, 180, 200]) {
      fireEvent.pointerMove(splitter, { clientX: x, pointerId: 1 })
    }
    expect(splitter).toHaveAttribute('aria-valuenow', String(LIST_WIDTH + 100))
    expect(screen.getByRole('region', { name: 'List' }).style.getPropertyValue('--list-size')).toBe(
      `${String(LIST_WIDTH + 100)}px`,
    )
    expect(setItem).not.toHaveBeenCalled()
    fireEvent.pointerUp(splitter, { clientX: 200, pointerId: 1 })
    expect(setItem).toHaveBeenCalledExactlyOnceWith('mailroom.ui.mail.listWidth', String(LIST_WIDTH + 100))
    expect(splitter).toHaveAttribute('aria-valuenow', String(LIST_WIDTH + 100))
    setItem.mockRestore()
  })

  it('leaves the reading pane its room, and widens back to the choice when there is room again', () => {
    vi.stubGlobal('ResizeObserver', FakeResizeObserver)
    window.localStorage.setItem('mailroom.ui.mail.listWidth', '700')
    render(<Panes />)
    const splitter = screen.getByRole('separator')
    layOut(1000)
    expect(splitter).toHaveAttribute('aria-valuemax', String(1000 - READING_MIN))
    expect(splitter).toHaveAttribute('aria-valuenow', String(1000 - READING_MIN))
    // Too narrow for both: the list keeps its narrowest.
    layOut(500)
    expect(splitter).toHaveAttribute('aria-valuemax', String(LIST_MIN))
    expect(splitter).toHaveAttribute('aria-valuenow', String(LIST_MIN))
    layOut(2000)
    expect(splitter).toHaveAttribute('aria-valuemax', String(LIST_MAX))
    expect(splitter).toHaveAttribute('aria-valuenow', '700')
  })

  it('reads a stored width out of bounds as within them', () => {
    window.localStorage.setItem('mailroom.ui.mail.listWidth', '5')
    render(<Panes />)
    expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow', String(LIST_MIN))
  })

  it('with the reading pane below, takes its share of the height until this device chooses one', async () => {
    vi.stubGlobal('ResizeObserver', FakeResizeObserver)
    const user = userEvent.setup()
    render(<Panes position="below" />)
    const splitter = screen.getByRole('separator')
    expect(splitter).toHaveAttribute('aria-orientation', 'horizontal')
    layOut(2000, 'offsetHeight')
    expect(splitter).toHaveAttribute('aria-valuenow', '800')
    expect(splitter).toHaveAttribute('aria-valuetext', '800 px high')
    splitter.focus()
    await user.keyboard('{ArrowDown}')
    expect(window.localStorage.getItem('mailroom.ui.mail.listHeight')).toBe('816')
    // The choice stands as the window grows; a double-click goes back to the share.
    layOut(2400, 'offsetHeight')
    expect(splitter).toHaveAttribute('aria-valuenow', '816')
    fireEvent.doubleClick(splitter)
    expect(splitter).toHaveAttribute('aria-valuenow', '960')
  })
})

describe('the list pane bounds', () => {
  const comfortable = ROW_ESTIMATE.comfortable
  const fiveRows = LIST_CHROME_HEIGHT + LIST_MIN_ROWS * comfortable

  it('across, leave the reading pane its width', () => {
    expect(listWidthBounds(null)).toEqual({ min: LIST_MIN, max: LIST_MAX, initial: LIST_WIDTH })
    expect(listWidthBounds(1000)).toEqual({ min: LIST_MIN, max: 1000 - READING_MIN, initial: LIST_WIDTH })
    expect(listWidthBounds(500).max).toBe(LIST_MIN)
  })

  it('down, start at a share of the room, with room for five rows at least', () => {
    // A tall window: the share holds the rows.
    expect(listHeightBounds(1400, comfortable)).toEqual({ min: fiveRows, max: 1400 - READING_MIN_HEIGHT, initial: 560 })
    // A laptop's: the share would hold fewer rows, so the list takes room for five.
    expect(listHeightBounds(800, comfortable)).toEqual({ min: fiveRows, max: 800 - READING_MIN_HEIGHT, initial: fiveRows })
    // Compact rows are shorter: five of them need less.
    expect(listHeightBounds(800, ROW_ESTIMATE.compact).min).toBe(LIST_CHROME_HEIGHT + LIST_MIN_ROWS * ROW_ESTIMATE.compact)
    // Not yet laid out: the five rows.
    expect(listHeightBounds(null, comfortable)).toEqual({ min: fiveRows, max: LIST_MAX_HEIGHT, initial: fiveRows })
  })

  it('down, leave the reading pane its height before the five rows', () => {
    expect(listHeightBounds(600, comfortable)).toEqual({ min: 360, max: 360, initial: 360 })
    // Too low for both: the list keeps its lowest.
    expect(listHeightBounds(300, comfortable)).toEqual({ min: LIST_MIN_HEIGHT, max: LIST_MIN_HEIGHT, initial: LIST_MIN_HEIGHT })
    expect(listHeightBounds(10_000, comfortable).max).toBe(LIST_MAX_HEIGHT)
  })
})

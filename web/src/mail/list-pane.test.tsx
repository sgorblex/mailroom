import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useRef, type ReactElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LIST_MAX, LIST_MIN, LIST_WIDTH, ListPane, READING_MIN } from './list-pane.tsx'

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

/** Lays the list and the reading pane out as sharing `room` pixels, and tells the observers. */
function layOut(room: number): void {
  const list = screen.getByRole('region', { name: 'List' })
  const pane = screen.getByRole('region', { name: 'Pane' })
  Object.defineProperty(list, 'offsetWidth', { configurable: true, value: room / 2 })
  Object.defineProperty(pane, 'offsetWidth', { configurable: true, value: room / 2 })
  act(() => {
    for (const observer of [...observers]) {
      observer()
    }
  })
}

function Panes(): ReactElement {
  const pane = useRef<HTMLElement>(null)
  return (
    <div>
      <ListPane id="list" label="List" className="flex" pane={pane}>
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
    expect(screen.getByRole('region', { name: 'List' }).style.getPropertyValue('--list-width')).toBe(`${String(LIST_WIDTH)}px`)
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
    expect(screen.getByRole('region', { name: 'List' }).style.getPropertyValue('--list-width')).toBe(
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
})

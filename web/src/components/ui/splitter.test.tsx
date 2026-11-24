import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState, type ReactElement } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { Splitter } from './splitter.tsx'

function Sized({
  initial = 400,
  onReset = () => undefined,
  onCommit = () => undefined,
}: {
  initial?: number
  onReset?: () => void
  onCommit?: (value: number) => void
}): ReactElement {
  const [value, setValue] = useState(initial)
  return (
    <>
      <div id="pane" />
      <Splitter
        value={value}
        min={300}
        max={500}
        onChange={setValue}
        onCommit={onCommit}
        onReset={onReset}
        controls="pane"
        label="Resize"
        valueText={`${String(value)} px`}
      />
    </>
  )
}

describe('Splitter', () => {
  it('is a focusable vertical separator naming its pane and its bounds', () => {
    render(<Sized />)
    const splitter = screen.getByRole('separator', { name: 'Resize' })
    expect(splitter).toHaveAttribute('tabindex', '0')
    expect(splitter).toHaveAttribute('aria-orientation', 'vertical')
    expect(splitter).toHaveAttribute('aria-controls', 'pane')
    expect(splitter).toHaveAttribute('aria-valuenow', '400')
    expect(splitter).toHaveAttribute('aria-valuemin', '300')
    expect(splitter).toHaveAttribute('aria-valuemax', '500')
    expect(splitter).toHaveAttribute('aria-valuetext', '400 px')
  })

  it('moves with the arrow keys, further with Shift, to the ends with Home and End, never past them', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<Sized onCommit={onCommit} />)
    const splitter = screen.getByRole('separator')
    splitter.focus()
    await user.keyboard('{ArrowRight}')
    expect(splitter).toHaveAttribute('aria-valuenow', '416')
    expect(onCommit).toHaveBeenLastCalledWith(416)
    await user.keyboard('{ArrowLeft}{ArrowLeft}')
    expect(splitter).toHaveAttribute('aria-valuenow', '384')
    await user.keyboard('{Shift>}{ArrowRight}{/Shift}')
    expect(splitter).toHaveAttribute('aria-valuenow', '448')
    await user.keyboard('{Shift>}{ArrowRight}{/Shift}')
    expect(splitter).toHaveAttribute('aria-valuenow', '500')
    await user.keyboard('{Home}')
    expect(splitter).toHaveAttribute('aria-valuenow', '300')
    await user.keyboard('{ArrowLeft}')
    expect(splitter).toHaveAttribute('aria-valuenow', '300')
    // A key that moves nothing commits nothing.
    expect(onCommit).toHaveBeenCalledTimes(6)
    await user.keyboard('{End}')
    expect(splitter).toHaveAttribute('aria-valuenow', '500')
  })

  it('follows a pointer drag within its bounds, commits where it ends once, and resets on a double-click', () => {
    const onReset = vi.fn()
    const onCommit = vi.fn()
    render(<Sized onReset={onReset} onCommit={onCommit} />)
    const splitter = screen.getByRole('separator')
    // jsdom has no pointer capture.
    splitter.setPointerCapture = vi.fn()
    fireEvent.pointerDown(splitter, { button: 0, clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(splitter, { clientX: 150, pointerId: 1 })
    expect(splitter).toHaveAttribute('aria-valuenow', '450')
    fireEvent.pointerMove(splitter, { clientX: 400, pointerId: 1 })
    expect(splitter).toHaveAttribute('aria-valuenow', '500')
    expect(onCommit).not.toHaveBeenCalled()
    fireEvent.pointerUp(splitter, { clientX: 400, pointerId: 1 })
    expect(onCommit).toHaveBeenCalledExactlyOnceWith(500)
    fireEvent.pointerMove(splitter, { clientX: 0, pointerId: 1 })
    expect(splitter).toHaveAttribute('aria-valuenow', '500')
    expect(splitter).toHaveFocus()
    fireEvent.doubleClick(splitter)
    expect(onReset).toHaveBeenCalledOnce()
    // A click that does not move commits nothing.
    fireEvent.pointerDown(splitter, { button: 0, clientX: 100, pointerId: 1 })
    fireEvent.pointerUp(splitter, { clientX: 100, pointerId: 1 })
    expect(onCommit).toHaveBeenCalledOnce()
  })
})

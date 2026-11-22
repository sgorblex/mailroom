import { act, render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { readDevicePref, useDevicePref, writeDevicePref } from './device-prefs.ts'

const isWidth = (value: unknown): value is number => typeof value === 'number' && value > 0

function Width({ label }: { label: string }): ReactElement {
  const [width, setWidth] = useDevicePref('list_width', 320, isWidth)
  return (
    <button type="button" onClick={() => { setWidth(width + 10); }}>
      {label} {width}
    </button>
  )
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('device preferences', () => {
  it('keeps a choice as JSON under its own name, and reads it back', () => {
    writeDevicePref('list_width', 400)
    expect(window.localStorage.getItem('mailroom.ui.list_width')).toBe('400')
    expect(readDevicePref('list_width', 320, isWidth)).toBe(400)
  })

  it('reads the fallback for nothing kept, bad JSON or a value that is not valid', () => {
    expect(readDevicePref('list_width', 320, isWidth)).toBe(320)
    window.localStorage.setItem('mailroom.ui.list_width', '{not json')
    expect(readDevicePref('list_width', 320, isWidth)).toBe(320)
    window.localStorage.setItem('mailroom.ui.list_width', '"wide"')
    expect(readDevicePref('list_width', 320, isWidth)).toBe(320)
  })

  it('reads the fallback when storage throws, and keeps a refused write until reload', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    expect(readDevicePref('pane_collapsed', false, (value): value is boolean => typeof value === 'boolean')).toBe(false)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    expect(() => {
      writeDevicePref('pane_collapsed', true)
    }).not.toThrow()
    expect(readDevicePref('pane_collapsed', false, (value): value is boolean => typeof value === 'boolean')).toBe(true)
    vi.restoreAllMocks()
    writeDevicePref('pane_collapsed', false)
    expect(window.localStorage.getItem('mailroom.ui.pane_collapsed')).toBe('false')
  })

  it("keeps every component in step, and follows another tab's write", () => {
    render(
      <>
        <Width label="first" />
        <Width label="second" />
      </>,
    )
    expect(screen.getByRole('button', { name: 'first 320' })).toBeInTheDocument()

    act(() => {
      screen.getByRole('button', { name: 'first 320' }).click()
    })
    expect(screen.getByRole('button', { name: 'second 330' })).toBeInTheDocument()

    act(() => {
      window.localStorage.setItem('mailroom.ui.list_width', '500')
      window.dispatchEvent(new StorageEvent('storage', { key: 'mailroom.ui.list_width', newValue: '500' }))
    })
    expect(screen.getByRole('button', { name: 'first 500' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'second 500' })).toBeInTheDocument()
  })
})

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLook, startLook } from './look.ts'

/** A `matchMedia` whose OS dark mode the test turns on and off, telling the listeners. */
function fakeSystemTheme(initiallyDark: boolean): { setDark: (dark: boolean) => void } {
  let dark = initiallyDark
  const listeners = new Set<() => void>()
  vi.stubGlobal('matchMedia', (query: string) => ({
    media: query,
    get matches() {
      return dark
    },
    addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
  }))
  return {
    setDark(next) {
      dark = next
      for (const listener of listeners) {
        listener()
      }
    },
  }
}

const root = document.documentElement
const themeColor = (): string | null => document.querySelector('meta[name="theme-color"]')?.getAttribute('content') ?? null

let stop: () => void = () => undefined

beforeEach(() => {
  root.className = ''
  delete root.dataset['accent']
  delete root.dataset['textSize']
})

afterEach(() => {
  stop()
  vi.unstubAllGlobals()
})

describe('the look', () => {
  it('starts from the copy this browser keeps, before anything renders', () => {
    fakeSystemTheme(false)
    window.localStorage.setItem('mailroom.ui.look', JSON.stringify({ theme: 'dark', accent: 'rose', text_size: 'large' }))
    stop = startLook()
    expect(root).toHaveClass('dark')
    expect(root.dataset['accent']).toBe('rose')
    expect(root.dataset['textSize']).toBe('large')
    expect(themeColor()).toBe('#0f1a18')
  })

  it('starts in the default look without a copy, or with one it cannot read', () => {
    fakeSystemTheme(false)
    window.localStorage.setItem('mailroom.ui.look', JSON.stringify({ theme: 'sepia' }))
    stop = startLook()
    expect(root).not.toHaveClass('dark')
    expect(root.dataset['accent']).toBe('default')
    expect(root.dataset['textSize']).toBe('default')
    expect(themeColor()).toBe('#f1f6f5')
  })

  it('shows a look at once and keeps it for the next load', () => {
    fakeSystemTheme(true)
    stop = startLook()
    setLook({ theme: 'light', accent: 'blue', text_size: 'small' })
    expect(root).not.toHaveClass('dark')
    expect(root.dataset['accent']).toBe('blue')
    expect(root.dataset['textSize']).toBe('small')
    expect(JSON.parse(window.localStorage.getItem('mailroom.ui.look') ?? 'null')).toEqual({ theme: 'light', accent: 'blue', text_size: 'small' })
  })

  it('follows the OS while the theme is system, and only then', () => {
    const system = fakeSystemTheme(false)
    stop = startLook()
    expect(root).not.toHaveClass('dark')
    system.setDark(true)
    expect(root).toHaveClass('dark')
    expect(themeColor()).toBe('#0f1a18')

    setLook({ theme: 'light', accent: 'default', text_size: 'default' })
    expect(root).not.toHaveClass('dark')
    system.setDark(false)
    system.setDark(true)
    expect(root).not.toHaveClass('dark')
  })
})

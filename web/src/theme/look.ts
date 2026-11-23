import { z } from 'zod'

import { ACCENTS, DEFAULT_APPEARANCE, TEXT_SIZES, THEMES, type Appearance } from '@/api/auth.ts'
import { readDevicePref, writeDevicePref } from '@/lib/device-prefs.ts'

/**
 * The part of the user's appearance that styles the whole page, set on <html>: `.dark`,
 * `data-accent` and `data-text-size`, which `index.css` reads, plus <meta name="theme-color">.
 * This browser keeps a copy so the next load starts in it before `/me` answers; the server's
 * value replaces it then (`LookSync`).
 */
export type Look = Pick<Appearance, 'theme' | 'accent' | 'text_size'>

/** The page background of each mode, as `index.css` has it (the contrast test checks they agree). */
export const BACKGROUNDS = { light: '#f1f6f5', dark: '#0f1a18' } as const

const STORED = 'look'
const DARK_QUERY = '(prefers-color-scheme: dark)'

const lookSchema = z.object({ theme: z.enum(THEMES), accent: z.enum(ACCENTS), text_size: z.enum(TEXT_SIZES) })
const isLook = (value: unknown): value is Look => lookSchema.safeParse(value).success

export const DEFAULT_LOOK: Readonly<Look> = Object.freeze({
  theme: DEFAULT_APPEARANCE.theme,
  accent: DEFAULT_APPEARANCE.accent,
  text_size: DEFAULT_APPEARANCE.text_size,
})

let current: Look = DEFAULT_LOOK

/** The look's own fields of an appearance. */
export function lookOf({ theme, accent, text_size }: Look): Look {
  return { theme, accent, text_size }
}

function systemDark(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia(DARK_QUERY).matches
}

function apply(look: Look): void {
  const root = document.documentElement
  const dark = look.theme === 'dark' || (look.theme === 'system' && systemDark())
  root.classList.toggle('dark', dark)
  root.dataset['accent'] = look.accent
  root.dataset['textSize'] = look.text_size
  let meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
  if (meta === null) {
    meta = document.createElement('meta')
    meta.name = 'theme-color'
    document.head.append(meta)
  }
  meta.content = dark ? BACKGROUNDS.dark : BACKGROUNDS.light
}

/** Shows `look` now and keeps it as this browser's copy for the next load. */
export function setLook(look: Look): void {
  current = lookOf(look)
  apply(current)
  writeDevicePref(STORED, current)
}

/**
 * Before the first render: shows this browser's copy of the look (else the default), and from
 * then on follows the OS's light or dark while the theme is `system`. Returns the way to stop.
 */
export function startLook(): () => void {
  current = readDevicePref(STORED, DEFAULT_LOOK, isLook)
  apply(current)
  if (typeof window.matchMedia !== 'function') {
    return () => undefined
  }
  const query = window.matchMedia(DARK_QUERY)
  const follow = (): void => {
    if (current.theme === 'system') {
      apply(current)
    }
  }
  query.addEventListener('change', follow)
  return () => {
    query.removeEventListener('change', follow)
  }
}

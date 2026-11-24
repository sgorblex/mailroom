/// <reference types="node" />
import { readFileSync } from 'node:fs'
import path from 'node:path'

import { describe, expect, it } from 'vitest'

import { ACCENTS } from '@/api/auth.ts'

import { BACKGROUNDS } from './look.ts'

type Tokens = Record<string, string>
type Mode = 'light' | 'dark'

/** The custom properties of each top-level rule of `index.css`, by where they apply. */
function rules(): { mode: Mode; accent: string | null; tokens: Tokens }[] {
  const css = readFileSync(path.resolve(import.meta.dirname, '../index.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
  const found = []
  for (const [, selectors = '', body = ''] of css.matchAll(/^([:.[][^{}@]*)\{([^{}]*)\}/gm)) {
    const tokens: Tokens = {}
    for (const [, name = '', value = ''] of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) {
      tokens[name] = value.trim().toLowerCase()
    }
    // `.dark[data-accent='x'], .dark [data-accent='x']` is one place: <html> or an element in it.
    const places = new Map<string, { mode: Mode; accent: string | null }>()
    for (const selector of selectors.split(',').map((part) => part.trim())) {
      const accent = /\[data-accent='(\w+)'\]/.exec(selector)?.[1] ?? null
      if (accent !== null || selector === ':root' || selector === ':root.dark') {
        const mode = selector.includes('.dark') ? 'dark' : 'light'
        places.set(`${mode} ${accent ?? ''}`, { mode, accent })
      }
    }
    for (const place of places.values()) {
      found.push({ ...place, tokens })
    }
  }
  return found
}

/** The tokens `<html class="dark?" data-accent="accent">` ends up with. */
function resolved(mode: Mode, accent: string): Tokens {
  const all = rules()
  const pick = (where: Mode, which: string | null): Tokens =>
    Object.assign({}, ...all.filter((rule) => rule.mode === where && rule.accent === which).map((rule) => rule.tokens)) as Tokens
  return mode === 'light'
    ? { ...pick('light', null), ...pick('light', accent) }
    : { ...pick('light', null), ...pick('dark', null), ...pick('light', accent), ...pick('dark', accent) }
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((start) => Number.parseInt(hex.slice(start, start + 2), 16) / 255)
  const [r = 0, g = 0, b = 0] = channels.map((c) => (c <= 0.039_28 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** WCAG 2's contrast ratio of two `#rrggbb` colours. */
function contrast(one: string, other: string): number {
  const [lighter, darker] = [luminance(one), luminance(other)].sort((a, b) => b - a)
  return ((lighter ?? 0) + 0.05) / ((darker ?? 0) + 0.05)
}

/** Text on its background: each needs AA's 4.5:1. */
const PAIRS: [string, string][] = [
  ['foreground', 'background'],
  ['foreground', 'card'],
  ['foreground', 'sidebar'],
  ['card-foreground', 'card'],
  ['popover-foreground', 'popover'],
  ['sidebar-foreground', 'sidebar'],
  ['secondary-foreground', 'secondary'],
  ['muted-foreground', 'background'],
  ['muted-foreground', 'card'],
  ['muted-foreground', 'muted'],
  ['muted-foreground', 'accent'],
  ['accent-foreground', 'accent'],
  ['primary-foreground', 'primary'],
  ['primary', 'background'],
  ['primary', 'card'],
  ['accent-soft-foreground', 'accent-soft'],
  ['accent-soft-foreground', 'card'],
  ['destructive', 'background'],
  ['destructive', 'card'],
  ['destructive-foreground', 'destructive'],
  ['destructive', 'popover'],
  ['destructive', 'accent'],
  ['success', 'background'],
  ['success', 'card'],
  ['warning', 'background'],
  ['warning', 'card'],
  ['star', 'background'],
  ['star', 'card'],
]

/** What is not text but must be seen against what is around it, the focus ring above all: 3:1 (WCAG 1.4.11). */
const NON_TEXT: [string, string][] = [
  ['ring', 'background'],
  ['ring', 'card'],
  ['ring', 'popover'],
  ['ring', 'sidebar'],
  ['ring', 'accent'],
  ['ring', 'muted'],
  ['primary', 'muted'],
  // The star mark on a selected row.
  ['star', 'accent'],
]

const ACCENT_TOKENS = ['primary', 'primary-foreground', 'accent', 'accent-soft', 'accent-soft-foreground', 'ring']

describe('the palette', () => {
  for (const mode of ['light', 'dark'] as const) {
    for (const accent of ACCENTS) {
      it(`meets WCAG AA in ${mode} with the ${accent} accent`, () => {
        const tokens = resolved(mode, accent)
        for (const [text, background] of PAIRS) {
          const ratio = contrast(tokens[text] ?? '', tokens[background] ?? '')
          expect(ratio, `${text} on ${background}`).toBeGreaterThanOrEqual(4.5)
        }
        for (const [part, background] of NON_TEXT) {
          const ratio = contrast(tokens[part] ?? '', tokens[background] ?? '')
          expect(ratio, `${part} on ${background}`).toBeGreaterThanOrEqual(3)
        }
      })
    }
  }

  it('gives every accent, light and dark, the same tokens', () => {
    for (const mode of ['light', 'dark'] as const) {
      for (const accent of ACCENTS) {
        const own = rules().filter((rule) => rule.mode === mode && rule.accent === accent)
        expect(own, `${accent} ${mode}`).toHaveLength(1)
        expect(Object.keys(own[0]?.tokens ?? {}), `${accent} ${mode}`).toEqual(expect.arrayContaining(ACCENT_TOKENS))
      }
    }
  })

  it('tints native checkboxes, radios and ranges with the accent', () => {
    const css = readFileSync(path.resolve(import.meta.dirname, '../index.css'), 'utf8')
    expect(css).toMatch(/accent-color:\s*var\(--primary\);/)
  })

  it('has the backgrounds the theme-color meta uses', () => {
    expect(resolved('light', 'default')['background']).toBe(BACKGROUNDS.light)
    expect(resolved('dark', 'default')['background']).toBe(BACKGROUNDS.dark)
  })
})

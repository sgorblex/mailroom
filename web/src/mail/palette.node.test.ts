import { afterEach, describe, expect, it, vi } from 'vitest'

import { matchItems, matchScore, storedRecents, storeRecent, type PaletteItem } from './palette.ts'

function item(id: string, label: string, detail?: string): PaletteItem {
  return { id, group: 'folders', label, detail, run: () => undefined }
}

describe('matchScore', () => {
  it('matches every word, the start of a word best, then anywhere, then its letters in order', () => {
    expect(matchScore('arch', 'Archive work')).toBe(3)
    expect(matchScore('chiv', 'Archive work')).toBe(2)
    expect(matchScore('acv', 'Archive work')).toBe(1)
    expect(matchScore('arch wo', 'Archive work')).toBe(6)
    expect(matchScore('arch home', 'Archive work')).toBeNull()
    expect(matchScore('', 'Archive')).toBe(0)
  })

  it('ignores case and accents, and splits folder paths into words', () => {
    expect(matchScore('ATTIVITA', 'Attività')).toBe(3)
    expect(matchScore('2025', 'Archive/2025')).toBe(3)
  })
})

describe('matchItems', () => {
  it('keeps what matches, best first, else in order', () => {
    const items = [item('a', 'Projects'), item('b', 'Old projects'), item('c', 'Inbox', 'home'), item('d', 'Sent')]
    expect(matchItems(items, 'proj').map((each) => each.id)).toEqual(['a', 'b'])
    expect(matchItems(items, 'jects').map((each) => each.id)).toEqual(['a', 'b'])
    expect(matchItems(items, 'inbox home').map((each) => each.id)).toEqual(['c'])
    expect(matchItems(items, '').map((each) => each.id)).toEqual(['a', 'b', 'c', 'd'])
  })
})

describe('recents', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('keeps the last five choices, newest first, each once', () => {
    const store = new Map<string, string>()
    vi.stubGlobal('window', {
      localStorage: {
        getItem: (key: string) => store.get(key) ?? null,
        setItem: (key: string, value: string) => store.set(key, value),
      },
    })
    expect(storedRecents()).toEqual([])
    for (const id of ['a', 'b', 'c', 'd', 'e', 'a', 'f']) {
      storeRecent(id)
    }
    expect(storedRecents()).toEqual(['f', 'a', 'e', 'd', 'c'])
  })

  it('has none, and keeps none, when storage is refused', () => {
    const refused = (): never => {
      throw new Error('SecurityError')
    }
    vi.stubGlobal('window', { localStorage: { getItem: refused, setItem: refused } })
    expect(() => {
      storeRecent('a')
    }).not.toThrow()
    expect(storedRecents()).toEqual([])
  })
})

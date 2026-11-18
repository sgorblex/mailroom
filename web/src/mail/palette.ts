/** What the command palette offers: a place to go to, or an action to run. */
export interface PaletteItem {
  /** Stable across visits, for the recents. */
  id: string
  group: 'actions' | 'folders' | 'accounts' | 'pages'
  label: string
  /** Shown beside the label and matched too: a folder's account. */
  detail?: string
  shortcut?: string
  run: () => void
}

const RECENTS_KEY = 'mailroom.mail.palette.recent'
const RECENTS = 5

/** Lower case, without accents: "È" finds "e". */
function folded(text: string): string {
  return text.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase()
}

/** `letters` in order inside `word`, not necessarily together. */
function inOrder(letters: string, word: string): boolean {
  let at = 0
  for (const letter of word) {
    if (letter === letters[at]) {
      at += 1
    }
  }
  return at === letters.length
}

/**
 * How well `query` matches `text`, by words: each of the query's must begin a word of the text (3),
 * else occur in it (2), else have its letters in order in one word (1); `null` when one does not.
 */
export function matchScore(query: string, text: string): number | null {
  const haystack = folded(text)
  const words = haystack.split(/[\s/._-]+/)
  let score = 0
  for (const part of folded(query).split(/\s+/)) {
    if (part === '') {
      continue
    }
    if (words.some((word) => word.startsWith(part))) {
      score += 3
    } else if (haystack.includes(part)) {
      score += 2
    } else if (words.some((word) => inOrder(part, word))) {
      score += 1
    } else {
      return null
    }
  }
  return score
}

/** The items `query` matches, best first, in their order where they match as well. */
export function matchItems(items: readonly PaletteItem[], query: string): PaletteItem[] {
  return items
    .map((item) => ({ item, score: matchScore(query, `${item.label} ${item.detail ?? ''}`) }))
    .filter((each): each is { item: PaletteItem; score: number } => each.score !== null)
    .sort((a, b) => b.score - a.score)
    .map((each) => each.item)
}

/** The ids of the items last chosen in this browser, newest first. */
export function storedRecents(): string[] {
  try {
    const raw = window.localStorage.getItem(RECENTS_KEY)
    const parsed: unknown = raw === null ? null : JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === 'string') : []
  } catch {
    return []
  }
}

export function storeRecent(id: string): void {
  try {
    const recents = [id, ...storedRecents().filter((each) => each !== id)].slice(0, RECENTS)
    window.localStorage.setItem(RECENTS_KEY, JSON.stringify(recents))
  } catch {
    // Storage refused (a private window): the palette forgets its recents, nothing else.
  }
}

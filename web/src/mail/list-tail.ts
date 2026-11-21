import type { MessageItem, MessagePage, SourceFailure } from '@/api/mail.ts'

/** The rows loaded after the first page, and the cursor that goes on after them. */
export interface Tail {
  rows: MessageItem[]
  failures: SourceFailure[]
  cursor: string | null
}

/**
 * What stays below the first page read again (`next`) of what was shown below and in the one it
 * replaces: the rows after the last one `next` still holds. Those before it that `next` lacks are
 * gone; those after it were pushed down by new mail, or loaded later. When `next` holds none of
 * them, the list starts over.
 */
export function keptTail(previous: MessagePage | undefined, tail: Tail | null, next: MessagePage | undefined): Tail | null {
  if (previous === undefined || next === undefined) {
    return null
  }
  const shown = [...previous.messages, ...(tail?.rows ?? [])]
  const fresh = new Set(next.messages.map((message) => message.ref))
  const last = shown.findLastIndex((message) => fresh.has(message.ref))
  const rows = last === -1 ? [] : shown.slice(last + 1)
  if (rows.length === 0) {
    return null
  }
  return { rows, failures: tail?.failures ?? [], cursor: tail === null ? previous.next_cursor : tail.cursor }
}

/** A row kept on show though the list read again lacks it, and the ref of the row it follows (`null`: the top). */
export interface Kept {
  row: MessageItem
  after: string | null
}

/**
 * The rows of `shown` that `next` lacks but `keep` holds on to, each after the nearest row above
 * it that is still on show (in `next`, or kept itself).
 */
export function keptRows(shown: readonly MessageItem[], next: readonly MessageItem[], keep: (row: MessageItem) => boolean): Kept[] {
  const stays = new Set(next.map((row) => row.ref))
  const kept: Kept[] = []
  let above: string | null = null
  for (const row of shown) {
    if (stays.has(row.ref)) {
      above = row.ref
    } else if (keep(row)) {
      kept.push({ row, after: above })
      above = row.ref
    }
  }
  return kept
}

/** `rows` with the `kept` ones they lack put back in place. */
export function withKept(rows: readonly MessageItem[], kept: readonly Kept[]): MessageItem[] {
  const present = new Set(rows.map((row) => row.ref))
  const following = new Map<string | null, MessageItem[]>()
  for (const { row, after } of kept) {
    if (!present.has(row.ref)) {
      following.set(after, [...(following.get(after) ?? []), row])
    }
  }
  if (following.size === 0) {
    return [...rows]
  }
  const out: MessageItem[] = []
  const place = (row: MessageItem): void => {
    out.push(row)
    for (const next of following.get(row.ref) ?? []) {
      place(next)
    }
  }
  for (const row of following.get(null) ?? []) {
    place(row)
  }
  for (const row of rows) {
    place(row)
  }
  return out
}

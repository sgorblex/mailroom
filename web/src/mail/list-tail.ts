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

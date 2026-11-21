import { MAX_QUOTE_CHARS } from '@/api/mail.ts'

/** Marks a message's text body (`message-view.tsx`) with its ref: where a reply's quote is taken from. */
const QUOTE_SOURCE = 'data-quote-source'

function sourceOf(node: Node | null): string | null {
  const element = node instanceof Element ? node : (node?.parentElement ?? null)
  return element?.closest(`[${QUOTE_SOURCE}]`)?.getAttribute(QUOTE_SOURCE) ?? null
}

/**
 * The text selected in the text body of the message `ref`, cut to the server's cap; `null` when
 * the selection is not all inside it. The HTML view's sandboxed frame cannot be read from here,
 * so a selection there is not seen and a reply quotes the whole message.
 */
export function selectedQuote(ref: string): string | null {
  const selection = document.getSelection()
  if (selection === null || selection.isCollapsed || sourceOf(selection.anchorNode) !== ref || sourceOf(selection.focusNode) !== ref) {
    return null
  }
  const text = selection.toString()
  if (text.trim() === '') {
    return null
  }
  // Cut by code points, as the server counts them, never inside a surrogate pair.
  return text.length > MAX_QUOTE_CHARS ? Array.from(text).slice(0, MAX_QUOTE_CHARS).join('') : text
}

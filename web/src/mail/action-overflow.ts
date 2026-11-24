/**
 * The order the message's action bar gives its actions up to its More menu when they do not fit
 * on one row, first first: To sort, Report spam, the move pickers, then Reply all and Forward; Mark
 * read or unread, Star, Trash and Archive only after them, on a bar too narrow even for those.
 * Reply and the More button itself always stay.
 */
export const OVERFLOW_ORDER: readonly string[] = [
  'toSort',
  'markSorted',
  'reportSpam',
  'move',
  'moveToAccount',
  'replyAll',
  'forward',
  'markRead',
  'markUnread',
  'star',
  'unstar',
  'trash',
  'archive',
]

/** One thing on the bar, in its order: an action's button (by its id) or a fixed part (anything else). */
export interface BarItem {
  id: string
  width: number
}

/**
 * The ids of the actions that go to the More menu for `items` to fit in `room` pixels, `gap` apart:
 * none when they all fit, else as few as will do, taken in `OVERFLOW_ORDER`.
 */
export function overflowingActions(items: readonly BarItem[], room: number, gap: number): Set<string> {
  const out = new Set<string>()
  const width = (shown: readonly BarItem[]): number =>
    shown.reduce((sum, item) => sum + item.width, 0) + gap * Math.max(0, shown.length - 1)
  let shown = items
  for (const id of OVERFLOW_ORDER) {
    if (width(shown) <= room) {
      break
    }
    if (shown.some((item) => item.id === id)) {
      out.add(id)
      shown = shown.filter((item) => item.id !== id)
    }
  }
  return out
}

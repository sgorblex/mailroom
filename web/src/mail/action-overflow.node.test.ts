import { describe, expect, it } from 'vitest'

import { overflowingActions, type BarItem } from './action-overflow.ts'

/** The bar of an open message: three replies, a separator, the triage icons and More. */
const BAR: BarItem[] = [
  { id: 'reply', width: 80 },
  { id: 'replyAll', width: 100 },
  { id: 'forward', width: 90 },
  { id: 'fixed 3', width: 9 },
  { id: 'archive', width: 32 },
  { id: 'reportSpam', width: 32 },
  { id: 'trash', width: 32 },
  { id: 'move', width: 32 },
  { id: 'moveToAccount', width: 32 },
  { id: 'star', width: 32 },
  { id: 'toSort', width: 32 },
  { id: 'markUnread', width: 32 },
  { id: 'fixed 12', width: 32 },
]
// 80 + 100 + 90 + 9 + 9 × 32 = 567, and 12 gaps of 4.
const FULL = 567 + 12 * 4

describe('the action bar overflow', () => {
  it('keeps every action on the bar when they fit', () => {
    expect(overflowingActions(BAR, FULL, 4)).toEqual(new Set())
  })

  it('gives up To sort first, then spam, the moves, Reply all and Forward', () => {
    expect(overflowingActions(BAR, FULL - 1, 4)).toEqual(new Set(['toSort']))
    expect(overflowingActions(BAR, FULL - 36 - 1, 4)).toEqual(new Set(['toSort', 'reportSpam']))
    expect(overflowingActions(BAR, FULL - 3 * 36 - 1, 4)).toEqual(new Set(['toSort', 'reportSpam', 'move', 'moveToAccount']))
    expect(overflowingActions(BAR, FULL - 4 * 36 - 1, 4)).toEqual(
      new Set(['toSort', 'reportSpam', 'move', 'moveToAccount', 'replyAll']),
    )
    expect(overflowingActions(BAR, FULL - 4 * 36 - 104 - 1, 4)).toEqual(
      new Set(['toSort', 'reportSpam', 'move', 'moveToAccount', 'replyAll', 'forward']),
    )
  })

  it('keeps Reply, read, star, trash and archive while the others make room', () => {
    // Reply, the separator, Archive, Trash, Star, Mark unread and More, with their gaps.
    const kept = 80 + 9 + 5 * 32 + 6 * 4
    const out = overflowingActions(BAR, kept, 4)
    expect(out).toEqual(new Set(['toSort', 'reportSpam', 'move', 'moveToAccount', 'replyAll', 'forward']))
  })

  it('gives those up too on a bar too narrow even for them, but never Reply or More', () => {
    const out = overflowingActions(BAR, 100, 4)
    expect(out).toEqual(
      new Set(['toSort', 'reportSpam', 'move', 'moveToAccount', 'replyAll', 'forward', 'markUnread', 'star', 'trash', 'archive']),
    )
    expect(out.has('reply')).toBe(false)
  })

  it('skips the actions the bar does not list', () => {
    const bar = BAR.filter((item) => item.id !== 'toSort' && item.id !== 'reportSpam')
    expect(overflowingActions(bar, FULL - 2 * 36 - 1, 4)).toEqual(new Set(['move']))
  })
})

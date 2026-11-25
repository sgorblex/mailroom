import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DEFAULT_APPEARANCE, type Appearance, type UserJson } from '@/api/auth.ts'
import { type FakeMailState, HOME, messageItem, refusal, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

import { SWIPE_SHARE } from './swipe-row.tsx'

const inbox = Array.from({ length: 3 }, (_, index) => messageItem(WORK, 'INBOX', 10 - index))

function state(extra: Partial<FakeMailState> = {}): FakeMailState {
  return {
    ...extra,
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 3 },
        { path: 'Archive', role: 'archive', unread: 0, total: 0 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 0 }],
    },
    messages: { '1/INBOX': inbox },
    views: {},
  }
}

function alice(swipes: Partial<Pick<Appearance, 'swipe_left' | 'swipe_right'>> = {}): UserJson {
  return {
    username: 'alice',
    is_admin: false,
    time_zone: null,
    mark_read_subfolders: true,
    index_max_age_days: null,
    conversations: true,
    undo_send_seconds: 10,
    appearance: { ...DEFAULT_APPEARANCE, ...swipes },
  }
}

/** Every row is this wide. */
const WIDTH = 400
/** How far a swipe must go to act without a fling. */
const THRESHOLD = WIDTH * SWIPE_SHARE

const ARCHIVE = '/api/v1/mail/messages/archive'
const TRASH = '/api/v1/mail/messages/trash'
const FLAG = '/api/v1/mail/messages/flag'

/** The part of the row with `subject` that a finger moves. */
async function surface(subject: string): Promise<HTMLElement> {
  const list = await screen.findByRole('list', { name: 'Messages' })
  const link = within(list).getByRole('link', { name: new RegExp(`${subject}Preview`) })
  const moved = link.parentElement
  if (moved === null) {
    throw new Error(`no row for ${subject}`)
  }
  return moved
}

/** A finger (or `pointerType`) going down on `element` and through `xs` sideways, `dy` down at each; lifted unless `lift` is false. */
function swipe(element: HTMLElement, xs: number[], { pointerType = 'touch', dy = 0, lift = true } = {}): void {
  const start = { pointerId: 1, pointerType, clientX: 200, clientY: 20 }
  fireEvent.pointerDown(element, start)
  let y = start.clientY
  for (const x of xs) {
    y += dy
    fireEvent.pointerMove(element, { ...start, clientX: start.clientX + x, clientY: y })
  }
  if (lift) {
    fireEvent.pointerUp(element, { ...start, clientX: start.clientX + (xs.at(-1) ?? 0), clientY: y })
  }
}

/** What shows behind the moved row: the action named `label`. */
const layer = (row: HTMLElement, label: string): HTMLElement => within(row.parentElement ?? row).getByText(label)

const calls = (fetchMock: ReturnType<typeof vi.fn>, path: string): unknown[] =>
  fetchMock.mock.calls.filter(([url]) => String(url).endsWith(path))

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 0, WIDTH, 80))
  // jsdom has no pointer capture.
  HTMLElement.prototype.setPointerCapture = vi.fn()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  Reflect.deleteProperty(HTMLElement.prototype, 'setPointerCapture')
})

describe('a swipe set to show the row actions', () => {
  const menu = (row_actions: Appearance['row_actions']): UserJson => ({
    ...alice(),
    appearance: { ...DEFAULT_APPEARANCE, swipe_left: 'menu', row_actions },
  })
  const bar = (row: HTMLElement): Element | null => row.querySelector('[data-row-actions]')

  it('leaves them on the row until a tap elsewhere, or Escape', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: menu(['star', 'trash']) })
    const row = await surface('Message 9')
    expect(bar(row)).toHaveClass('pointer-coarse:sr-only')

    swipe(row, [-20, -80, -160])
    expect(row.style.transform).toBe('')
    expect(bar(row)).not.toHaveClass('pointer-coarse:sr-only')
    expect(bar(row)).not.toHaveClass('opacity-0')
    fireEvent.pointerDown(document.body)
    await waitFor(() => {
      expect(bar(row)).toHaveClass('pointer-coarse:sr-only')
    })

    swipe(row, [-20, -80, -160])
    expect(bar(row)).not.toHaveClass('opacity-0')
    fireEvent.keyDown(document.body, { key: 'Escape' })
    await waitFor(() => {
      expect(bar(row)).toHaveClass('opacity-0')
    })
    expect(calls(fetchMock, TRASH)).toHaveLength(0)
  })

  it('runs the one tapped, on that row, and hides them', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: menu(['star']) })
    const row = await surface('Message 9')
    swipe(row, [-20, -80, -160])
    const star = within(row).getByRole('button', { name: 'Star' })
    fireEvent.pointerDown(star, { pointerId: 2, pointerType: 'touch' })
    expect(bar(row)).not.toHaveClass('opacity-0')
    fireEvent.click(star, { detail: 1 })
    await waitFor(() => {
      expect(calls(fetchMock, FLAG)).toHaveLength(1)
    })
    expect(bar(row)).toHaveClass('opacity-0')
  })

  it('does not move towards that side when the row has none', async () => {
    stubMailApi(state())
    renderMailApp('/mail', { user: menu([]) })
    const row = await surface('Message 9')
    swipe(row, [-20, -80, -300])
    expect(row.style.transform).toBe('')
    expect(bar(row)).toBeNull()
  })
})

describe('swiping a row', () => {
  it('archives a row swiped right past the threshold, with Undo', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: alice() })
    const row = await surface('Message 9')

    swipe(row, [20, 80, 160], { lift: false })
    expect(row).toHaveStyle({ transform: 'translateX(160px)' })
    expect(layer(row, 'Archive')).toHaveClass('bg-primary')
    fireEvent.pointerUp(row, { pointerId: 1, pointerType: 'touch', clientX: 360, clientY: 20 })

    await waitFor(() => {
      expect(calls(fetchMock, ARCHIVE)).toHaveLength(1)
    })
    expect(await screen.findByRole('button', { name: 'Undo' })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('link', { name: /Message 9Preview/ })).not.toBeInTheDocument()
    })
  })

  it('moves a row swiped left to Trash', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: alice() })
    const row = await surface('Message 9')

    swipe(row, [-20, -80, -160])
    await waitFor(() => {
      expect(calls(fetchMock, TRASH)).toHaveLength(1)
    })
    expect(calls(fetchMock, ARCHIVE)).toHaveLength(0)
    expect(await screen.findByRole('button', { name: 'Undo' })).toBeInTheDocument()
  })

  it('runs the chosen action, such as Star, and the row stays', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: alice({ swipe_right: 'star' }) })
    const row = await surface('Message 9')

    swipe(row, [20, 80, 160], { lift: false })
    expect(layer(row, 'Star')).toHaveClass('bg-primary')
    fireEvent.pointerUp(row, { pointerId: 1, pointerType: 'touch', clientX: 360, clientY: 20 })
    await waitFor(() => {
      expect(calls(fetchMock, FLAG)).toHaveLength(1)
    })
    expect(row).not.toHaveStyle({ transform: 'translateX(160px)' })
    expect(screen.getByRole('link', { name: /Message 9Preview/ })).toBeInTheDocument()
  })

  it('acts on a fast fling shorter than the threshold', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: alice() })
    const row = await surface('Message 9')

    // 10 ms between each event: 30 px in each is 3 px per ms, well past a fling's speed.
    let now = Date.now()
    vi.spyOn(Date, 'now').mockImplementation(() => (now += 10))
    swipe(row, [20, 50, 80])
    vi.mocked(Date.now).mockRestore()
    expect(80).toBeLessThan(THRESHOLD)
    await waitFor(() => {
      expect(calls(fetchMock, ARCHIVE)).toHaveLength(1)
    })
  })

  it('keeps the action in its colour while the move runs, and brings the row back when it fails', async () => {
    const fetchMock = stubMailApi(state({ refuse: { [`POST ${ARCHIVE}`]: refusal(429, 'busy') } }))
    renderMailApp('/mail', { user: alice() })
    const row = await surface('Message 9')

    swipe(row, [20, 80, 160])
    expect(row).toHaveStyle({ transform: `translateX(${String(WIDTH)}px)` })
    expect(layer(row, 'Archive')).toHaveClass('bg-primary')
    await waitFor(() => {
      expect(calls(fetchMock, ARCHIVE)).toHaveLength(1)
    })
    await waitFor(() => {
      expect(row.style.transform).toBe('')
    })
    expect(screen.getByRole('link', { name: /Message 9Preview/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeInTheDocument()
  })

  it('opens nothing with the click a swipe ends in, but a keyboard click still opens the message', async () => {
    stubMailApi(state())
    const history = renderMailApp('/mail', { user: alice({ swipe_right: 'star' }) })
    const row = await surface('Message 9')
    const link = screen.getByRole('link', { name: /Message 9Preview/ })

    // Short of the threshold: it goes back, and the pointer click after it opens nothing.
    swipe(row, [15, 30])
    fireEvent.click(link, { detail: 1 })
    expect(history.location.search).not.toContain('message=')

    // Enter on the link after a swipe that acted.
    swipe(row, [20, 80, 160])
    fireEvent.click(link, { detail: 0 })
    await waitFor(() => {
      expect(history.location.search).toContain(`message=${encodeURIComponent(String(inbox[1]?.ref))}`)
    })
  })

  it('does not move towards a side whose action is unavailable on the row', async () => {
    const noArchive = state()
    noArchive.folders[WORK] = noArchive.folders[WORK]?.filter((folder) => folder.role !== 'archive') ?? []
    const fetchMock = stubMailApi({ ...noArchive, keywords: { [WORK]: false } })
    renderMailApp('/mail', { user: alice({ swipe_left: 'handled' }) })
    const row = await surface('Message 9')

    // No Archive folder.
    swipe(row, [20, 80, 300])
    expect(row.style.transform).toBe('')
    // Handled on a server without keywords.
    swipe(row, [-20, -80, -300])
    expect(row.style.transform).toBe('')
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(calls(fetchMock, ARCHIVE)).toHaveLength(0)
    expect(calls(fetchMock, FLAG)).toHaveLength(0)
  })

  it('does not trash a row already in Trash', async () => {
    const trashed = messageItem(WORK, 'Trash', 20)
    const inTrash = state()
    inTrash.messages['1/Trash'] = [trashed]
    const fetchMock = stubMailApi(inTrash)
    renderMailApp('/mail?account=1&folder=Trash', { user: alice() })
    const row = await surface('Message 20')
    // The list may come before the folders that say this one is Trash.
    await screen.findByRole('treeitem', { name: /^Trash/ })

    swipe(row, [-20, -80, -300])
    expect(row.style.transform).toBe('')
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(calls(fetchMock, TRASH)).toHaveLength(0)
  })

  it('goes back short of the threshold, or when pulled back, and acts on nothing', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: alice() })
    const row = await surface('Message 9')

    swipe(row, [15, 30], { lift: false })
    expect(row).toHaveStyle({ transform: 'translateX(30px)' })
    expect(layer(row, 'Archive')).toHaveClass('bg-muted')
    fireEvent.pointerUp(row, { pointerId: 1, pointerType: 'touch', clientX: 230, clientY: 20 })
    expect(row.style.transform).toBe('')

    // Past a fling's length, but the finger goes back before it lifts.
    swipe(row, [20, 100, 90])
    expect(row.style.transform).toBe('')
    expect(row.previousElementSibling).toBeNull()
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(calls(fetchMock, ARCHIVE)).toHaveLength(0)
  })

  it('leaves a vertical move to the scroll, and the row has pan-y', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: alice() })
    const row = await surface('Message 9')
    expect(row).toHaveClass('touch-pan-y')

    swipe(row, [5, 10, 200], { dy: 30 })
    expect(row.style.transform).toBe('')
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(calls(fetchMock, ARCHIVE)).toHaveLength(0)
  })

  it('does not move towards a side whose action is none', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: alice({ swipe_left: 'none' }) })
    const row = await surface('Message 9')

    swipe(row, [-20, -80, -300])
    expect(row.style.transform).toBe('')
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(calls(fetchMock, TRASH)).toHaveLength(0)
  })

  it('does not swipe at all when both sides are none', async () => {
    stubMailApi(state())
    renderMailApp('/mail', { user: alice({ swipe_left: 'none', swipe_right: 'none' }) })
    const row = await surface('Message 9')
    expect(row).not.toHaveClass('touch-pan-y')

    swipe(row, [20, 80, 300])
    expect(row.style.transform).toBe('')
  })

  it('ignores a mouse drag', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: alice() })
    const row = await surface('Message 9')

    swipe(row, [20, 80, 300], { pointerType: 'mouse' })
    expect(row.style.transform).toBe('')
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(calls(fetchMock, ARCHIVE)).toHaveLength(0)
  })
})

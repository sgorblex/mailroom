import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DEFAULT_APPEARANCE, type Appearance, type UserJson } from '@/api/auth.ts'
import { LG_QUERY } from '@/lib/media-query.ts'
import { bodiesTo, type FakeMailState, HOME, messageItem, messageView, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

import { LIST_CHROME_HEIGHT, LIST_MIN_ROWS, ROW_ESTIMATE } from './list-size.ts'

const inbox = Array.from({ length: 5 }, (_, index) => messageItem(WORK, 'INBOX', 10 - index))
const opened = inbox[1] ?? messageItem(WORK, 'INBOX', 0)

function state(): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 5 }],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 0 }],
    },
    messages: { '1/INBOX': inbox, '2/INBOX': [] },
    views: { [opened.ref]: messageView(opened) },
  }
}

function userWith(reading_pane: Appearance['reading_pane']): UserJson {
  return {
    username: 'alice',
    is_admin: false,
    time_zone: null,
    mark_read_subfolders: true,
    index_max_age_days: null,
    conversations: false,
    undo_send_seconds: 10,
    appearance: { ...DEFAULT_APPEARANCE, reading_pane },
  }
}

/** A phone's window: narrower than `lg`. */
function narrowWindow(): void {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query !== LG_QUERY && !query.includes('min-width'),
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }))
}

const listPane = (): HTMLElement => screen.getByRole('region', { name: 'Messages' })
const readingPane = (): HTMLElement => screen.getByRole('region', { name: 'Message' })
const back = (): HTMLElement | null => screen.queryByRole('link', { name: 'Back to the list' })

async function openMessage(): Promise<void> {
  const list = await screen.findByRole('list', { name: 'Messages' })
  await userEvent.click(within(list).getByRole('link', { name: /Message 9/ }))
  expect(await screen.findByRole('heading', { name: 'Message 9' })).toBeInTheDocument()
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the reading pane', () => {
  it('sits right of the list by default, with the divider sizing its width and no Back', async () => {
    stubMailApi(state())
    renderMailApp('/mail')
    await openMessage()

    expect(screen.getByRole('separator', { name: 'Resize the message list' })).toHaveAttribute('aria-orientation', 'vertical')
    expect(listPane()).toHaveClass('lg:w-(--list-size)')
    expect(listPane()).not.toHaveAttribute('inert')
    expect(readingPane()).toHaveClass('lg:flex')
    expect(back()).not.toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(screen.getByRole('heading', { name: 'Message 9' })).toBeInTheDocument()
  })

  it('sits below the list, the divider sizing its height, kept for this device', async () => {
    stubMailApi(state())
    renderMailApp('/mail', { user: userWith('below') })
    await openMessage()

    const divider = screen.getByRole('separator', { name: 'Resize the message list' })
    expect(divider).toHaveAttribute('aria-orientation', 'horizontal')
    expect(listPane()).toHaveClass('lg:h-(--list-size)')
    divider.focus()
    await userEvent.keyboard('{ArrowDown}')
    // Not laid out (jsdom): the list starts with room for its five rows.
    const fiveRows = LIST_CHROME_HEIGHT + LIST_MIN_ROWS * ROW_ESTIMATE.comfortable
    expect(window.localStorage.getItem('mailroom.ui.mail.listHeight')).toBe(String(fiveRows + 16))
    expect(back()).not.toBeInTheDocument()
  })

  it('below the list, puts Compose, the search and the shortcut sheet on one row', async () => {
    stubMailApi(state())
    renderMailApp('/mail', { user: userWith('below') })
    await openMessage()

    const search = screen.getByRole('search', { name: 'Search mail' })
    expect(search).toHaveClass('flex-wrap')
    expect(search).not.toHaveClass('flex-col')
    const row = search.parentElement?.parentElement
    expect(row).not.toHaveClass('flex-col')
    expect(within(row ?? document.body).getByRole('button', { name: 'Compose' })).toBeInTheDocument()
    // The sheet's button comes last, after the search, in the order focus takes.
    expect(row?.lastElementChild).toBe(screen.getByRole('button', { name: 'Keyboard shortcuts' }))
  })

  it('right of the list, keeps the search under Compose', async () => {
    stubMailApi(state())
    renderMailApp('/mail')
    await openMessage()

    expect(screen.getByRole('search', { name: 'Search mail' })).toHaveClass('flex-col')
  })

  it('off, opens a message over the list with Back, and keeps the list under it', async () => {
    stubMailApi(state())
    renderMailApp('/mail', { user: userWith('off') })
    const list = await screen.findByRole('list', { name: 'Messages' })
    expect(screen.queryByRole('separator')).not.toBeInTheDocument()
    expect(readingPane()).toHaveClass('lg:hidden')
    await openMessage()

    // The list stays laid out (and scrolled) under the message, out of reach.
    expect(listPane()).toHaveAttribute('inert')
    expect(listPane()).toHaveClass('invisible')
    expect(list).toBeInTheDocument()
    expect(readingPane()).toHaveClass('lg:absolute', 'lg:inset-0')
    await userEvent.click(back() ?? document.body)
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Message 9' })).not.toBeInTheDocument()
    })
    expect(listPane()).not.toHaveAttribute('inert')
    expect(within(list).getByRole('link', { name: /Message 9/ })).toHaveFocus()
  })

  it('off, goes back to the list on Escape or u', async () => {
    stubMailApi(state())
    renderMailApp('/mail', { user: userWith('off') })
    await openMessage()
    await userEvent.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Message 9' })).not.toBeInTheDocument()
    })

    await openMessage()
    await userEvent.keyboard('u')
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Message 9' })).not.toBeInTheDocument()
    })
  })

  it('off, keeps the toolbar acting on the open message above it', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: userWith('off') })
    await openMessage()

    const toolbar = within(readingPane()).getByRole('group', { name: 'Message actions' })
    expect(within(listPane()).queryByRole('group', { name: 'Message actions' })).not.toBeInTheDocument()
    // `u` goes back here, so Mark unread names no shortcut.
    expect(within(toolbar).getByRole('button', { name: 'Mark as unread' })).not.toHaveAttribute('aria-keyshortcuts')
    await userEvent.click(within(toolbar).getByRole('button', { name: 'Star' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toEqual([{ refs: [opened.ref], flagged: true }])
    })
  })

  it('off, marks a selection unread on u and keeps the message open', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: userWith('off') })
    await openMessage()

    await userEvent.keyboard('x')
    await userEvent.keyboard('u')
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toEqual([{ refs: [opened.ref], seen: false }])
    })
    expect(screen.getByRole('heading', { name: 'Message 9' })).toBeInTheDocument()
  })

  it('off, puts the focus back on Compose when the composer closes', async () => {
    stubMailApi(state())
    renderMailApp('/mail', { user: userWith('off') })
    await screen.findByRole('list', { name: 'Messages' })
    await userEvent.keyboard('c')
    const form = await screen.findByRole('form', { name: 'New message' })
    expect(listPane()).toHaveAttribute('inert')

    await userEvent.click(within(form).getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(screen.queryByRole('form')).not.toBeInTheDocument()
    })
    expect(listPane()).not.toHaveAttribute('inert')
    expect(screen.getByRole('button', { name: 'Compose' })).toHaveFocus()
  })

  it('opens a message in full view on a phone, whatever the choice', async () => {
    narrowWindow()
    stubMailApi(state())
    renderMailApp('/mail')
    await openMessage()

    expect(back()).toBeInTheDocument()
    // The phone's own layout keeps the list's toolbar above the message: nothing is made inert.
    expect(listPane()).not.toHaveAttribute('inert')
    await userEvent.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Message 9' })).not.toBeInTheDocument()
    })
  })
})

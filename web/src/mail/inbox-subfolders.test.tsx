import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { UserJson } from '@/api/auth.ts'
import { expectNoAxeViolations } from '@/test/axe.ts'
import { FakeEventSource, stubEventSource } from '@/test/fake-event-source.ts'
import { bodiesTo, HOME, messageItem, queriesTo, stubMailApi, WORK, type FakeMailState } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const LIST = '/api/v1/mail/views/all_inboxes/messages'
const SAVE = '/api/v1/auth/me/inbox-subfolders'
const ALICE: UserJson = {
  username: 'alice',
  is_admin: false,
  time_zone: null,
  mark_read_subfolders: true,
  index_max_age_days: null,
  conversations: false,
  undo_send_seconds: 10,
}

function state(fields: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 1, total: 1 },
        { path: 'INBOX/Lists', role: null, unread: 4, total: 4 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 2, total: 2 }],
    },
    messages: {
      '1/INBOX': [messageItem(WORK, 'INBOX', 3, { subject: 'From work' })],
      '1/INBOX/Lists': [messageItem(WORK, 'INBOX/Lists', 4, { subject: 'A list post' })],
      '2/INBOX': [messageItem(HOME, 'INBOX', 2, { subject: 'From home' })],
    },
    views: {},
    savedViews: [],
    searchResult: { messages: [messageItem(WORK, 'INBOX/Lists', 4, { subject: 'Found' })], next_cursor: null, failures: [] },
    ...fields,
  }
}

const toggle = (): Promise<HTMLElement> => screen.findByRole('button', { name: 'Include subfolders' })
const box = (): HTMLElement => screen.getByRole('searchbox', { name: 'Search terms' })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('All inboxes with subfolders', () => {
  it('is off until chosen, kept for the user, and lists and counts them once on', async () => {
    const api = state()
    const fetchMock = stubMailApi(api)
    renderMailApp('/mail?view=all_inboxes', { user: ALICE })
    await screen.findByText('From home')
    expect(await toggle()).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByText('A list post')).not.toBeInTheDocument()
    const views = screen.getByRole('list', { name: 'Views' })
    expect(await within(views).findByRole('link', { name: 'All inboxes, 3 unread' })).toBeInTheDocument()

    await userEvent.click(await toggle())
    expect(await screen.findByText('A list post')).toBeInTheDocument()
    expect(await toggle()).toHaveAttribute('aria-pressed', 'true')
    expect(bodiesTo(fetchMock, SAVE)).toEqual([{ inbox_subfolders: true }])
    expect(queriesTo(fetchMock, LIST).map((query) => query.get('subfolders'))).toEqual([null, 'true'])
    expect(within(views).getByRole('link', { name: 'All inboxes, 7 unread' })).toBeInTheDocument()
    await expectNoAxeViolations()

    // A refresh syncs them too.
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/refresh')).toEqual([{ view: 'all_inboxes', subfolders: true }])
    })
    await userEvent.click(await toggle())
    await waitFor(() => {
      expect(screen.queryByText('A list post')).not.toBeInTheDocument()
    })
  })

  it('says when the choice could not be saved, and stays as it was', async () => {
    const api = state({ failSave: true })
    stubMailApi(api)
    renderMailApp('/mail?view=all_inboxes', { user: ALICE })
    await userEvent.click(await toggle())
    expect(await screen.findAllByText(/Could not save the choice/)).not.toHaveLength(0)
    expect(await toggle()).toHaveAttribute('aria-pressed', 'false')
  })

  it('is not offered in a folder or another view', async () => {
    stubMailApi(state())
    renderMailApp('/mail', { user: ALICE })
    await screen.findByText('From work')
    expect(screen.queryByRole('button', { name: 'Include subfolders' })).not.toBeInTheDocument()
  })

  it('searches them, and goes back', async () => {
    const fetchMock = stubMailApi(state())
    const history = renderMailApp('/mail?view=all_inboxes', { user: { ...ALICE, inbox_subfolders: true } })
    await screen.findByText('A list post')
    expect(screen.getByText('Searching in: All inboxes, with subfolders')).toBeInTheDocument()
    await userEvent.type(box(), 'invoice{Enter}')
    await screen.findByRole('list', { name: 'Search results' })
    expect(history.location.search).toContain('view=all_inboxes')
    await userEvent.click(screen.getByRole('button', { name: 'Search all mail' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Back to All inboxes, with subfolders' }))
    await screen.findByText('Searching in: All inboxes, with subfolders')
    expect(bodiesTo(fetchMock, '/api/v1/mail/search')).toEqual([
      { view: 'all_inboxes', subfolders: true, text: 'invoice' },
      { account: '*', folder: '*', text: 'invoice' },
      { view: 'all_inboxes', subfolders: true, text: 'invoice' },
    ])
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/refresh')).toEqual([{ view: 'all_inboxes', subfolders: true }])
    })
    await userEvent.click(screen.getByRole('button', { name: 'Clear search' }))
    expect(await screen.findByText('A list post')).toBeInTheDocument()
  })

  it('reads a search kept in the URL by the choice as it is now', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp(`/mail?view=all_inboxes&q=${encodeURIComponent(JSON.stringify({ view: 'all_inboxes', unread: true }))}`, { user: ALICE })
    await screen.findByRole('list', { name: 'Search results' })
    expect(bodiesTo(fetchMock, '/api/v1/mail/search')).toEqual([{ view: 'all_inboxes', unread: true }])
    expect(screen.getByText('Searching in: All inboxes')).toBeInTheDocument()
  })

  /** A change in a subfolder while All inboxes is listed; how its list was read before and since. */
  async function changeInSubfolder(included: boolean): Promise<{ before: number; reads: () => number; views: HTMLElement }> {
    stubEventSource()
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail?view=all_inboxes', { user: { ...ALICE, inbox_subfolders: included } })
    await screen.findByText('From home')
    const views = screen.getByRole('list', { name: 'Views' })
    await within(views).findByRole('link', { name: `All inboxes, ${included ? '7' : '3'} unread` })
    const reads = (): number => queriesTo(fetchMock, LIST).length
    const before = reads()
    const source = FakeEventSource.current()
    act(() => {
      source?.open()
      source?.send('folder_changed', { account: WORK, folder: 'INBOX/Lists', unread: 5, total: 5 })
    })
    return { before, reads, views }
  }

  it('follows a change in a subfolder it takes in', async () => {
    const { before, reads, views } = await changeInSubfolder(true)
    await waitFor(() => {
      expect(reads()).toBeGreaterThan(before)
    })
    expect(await within(views).findByRole('link', { name: 'All inboxes, 8 unread' })).toBeInTheDocument()
  })

  it('leaves out a change in a subfolder it does not take in', async () => {
    const { before, reads } = await changeInSubfolder(false)
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)))
    expect(reads()).toBe(before)
  })
})

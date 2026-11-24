import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SavedView } from '@/api/mail.ts'
import { FakeEventSource, stubEventSource } from '@/test/fake-event-source.ts'
import { bodiesTo, HOME, messageItem, queriesTo, stubMailApi, WORK, type FakeMailState } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const SEARCH = '/api/v1/mail/search'
const LISTS: SavedView = {
  id: 4,
  name: 'Lists',
  sources: [
    { account_id: WORK, folder: 'Lists' },
    { account_id: HOME, folder: 'INBOX' },
  ],
  query: null,
  position: 0,
}
const UNREAD: SavedView = { id: 5, name: 'Unread at home', sources: null, query: 'is:unread in:Archive account:home', position: 1 }
const BROKEN: SavedView = { id: 6, name: 'Broken', sources: null, query: 'account:nobody', position: 2 }

function state(fields: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 1 },
        { path: 'Lists', role: null, unread: 0, total: 1 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 1 }],
    },
    messages: {
      '1/INBOX': [messageItem(WORK, 'INBOX', 1, { subject: 'From work' })],
      '1/Lists': [messageItem(WORK, 'Lists', 1, { subject: 'A list post' })],
      '2/INBOX': [messageItem(HOME, 'INBOX', 1, { subject: 'From home' })],
    },
    views: {},
    savedViews: [LISTS, UNREAD, BROKEN],
    searchResult: { messages: [messageItem(WORK, 'INBOX', 1, { subject: 'Found' })], next_cursor: null, failures: [] },
    ...fields,
  }
}

const box = (): HTMLElement => screen.getByRole('searchbox', { name: 'Search terms' })
const button = (name: string): HTMLElement => screen.getByRole('button', { name })
const results = (): Promise<HTMLElement> => screen.findByRole('list', { name: 'Search results' })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('where a search is made', () => {
  it('is the folder shown, said beside the box, widened to all mail and narrowed back', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail?account=1&folder=Lists')
    await screen.findByText('A list post')

    expect(screen.getByText('Searching in: Lists · work')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Search all mail' })).not.toBeInTheDocument()
    await userEvent.type(box(), 'invoice{Enter}')
    await results()
    await userEvent.click(button('Search all mail'))
    expect(await screen.findByText('Searching in: All mail (not Junk or Trash)')).toBeInTheDocument()
    expect(box()).toHaveValue('invoice in:* account:*')
    expect(screen.queryByRole('button', { name: 'Search all mail' })).not.toBeInTheDocument()
    await userEvent.click(button('Back to Lists · work'))
    expect(await screen.findByText('Searching in: Lists · work')).toBeInTheDocument()
    expect(box()).toHaveValue('invoice')
    expect(bodiesTo(fetchMock, SEARCH)).toEqual([
      { account: WORK, folder: 'Lists', text: 'invoice' },
      { account: '*', folder: '*', text: 'invoice' },
      { account: WORK, folder: 'Lists', text: 'invoice' },
    ])
    await userEvent.click(button('Clear search'))
    expect(await screen.findByText('A list post')).toBeInTheDocument()
  })

  it('is INBOX of every account from All inboxes, which clearing goes back to', async () => {
    const fetchMock = stubMailApi(state())
    const history = renderMailApp('/mail?account=1&view=all_inboxes')
    await screen.findByText('From home')

    expect(screen.getByText('Searching in: All inboxes')).toBeInTheDocument()
    await userEvent.type(box(), 'invoice{Enter}')
    await results()
    expect(bodiesTo(fetchMock, SEARCH)).toEqual([{ account: '*', folder: 'INBOX', text: 'invoice' }])
    expect(box()).toHaveValue('invoice')
    await userEvent.click(button('Search all mail'))
    expect(await screen.findByRole('button', { name: 'Back to All inboxes' })).toBeInTheDocument()
    expect(box()).toHaveValue('invoice in:*')
    await userEvent.click(button('Clear search'))
    expect(await screen.findByText('From home')).toBeInTheDocument()
    expect(history.location.search).toContain('view=all_inboxes')
  })

  it("is the folders of a saved view, until the line names another", async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail?account=1&view=4')
    await screen.findByText('A list post')

    expect(await screen.findByText('Searching in: Lists')).toBeInTheDocument()
    await userEvent.type(box(), 'invoice{Enter}')
    await results()
    expect(box()).toHaveValue('invoice')
    expect(button('Save as view')).toHaveAttribute('title', expect.stringContaining('view of folders'))
    await userEvent.click(button('More options'))
    expect(screen.getByLabelText('Account')).toHaveDisplayValue("Lists's accounts")
    expect(screen.getByLabelText('Folder')).toHaveDisplayValue("Lists's folders")
    await userEvent.selectOptions(screen.getByLabelText('Account'), 'home')
    expect(box()).toHaveValue('invoice account:home')
    expect(screen.getByText('Searching in: Inbox · home')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Folder'), "Lists's folders")
    expect(box()).toHaveValue('invoice')
    await userEvent.click(button('Search all mail'))
    expect(await screen.findByText('Searching in: All mail (not Junk or Trash)')).toBeInTheDocument()
    await userEvent.click(button('Back to Lists'))
    await screen.findByText('Searching in: Lists')
    expect(bodiesTo(fetchMock, SEARCH)).toEqual([
      { view: 4, text: 'invoice' },
      { account: '*', folder: '*', text: 'invoice' },
      { view: 4, text: 'invoice' },
    ])
  })

  it('refreshes, follows and acts on a search in a view as on the view', async () => {
    stubEventSource()
    const fetchMock = stubMailApi(
      state({ searchResult: { messages: [messageItem(WORK, 'Lists', 1, { subject: 'Found' })], next_cursor: 'more', failures: [] } }),
    )
    renderMailApp(`/mail?view=4&q=${encodeURIComponent(JSON.stringify({ view: 4, unread: true }))}`)
    await results()
    await screen.findByText('Searching in: Lists')
    await userEvent.click(button('Refresh'))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/refresh')).toEqual([{ view: 4 }])
    })
    // Every row selected, with more to load: no count of all that matches is asked.
    for (const row of within(await results()).getAllByRole('checkbox', { name: 'Select Found' })) {
      await userEvent.click(row)
    }
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/count')).toEqual([])

    const read = bodiesTo(fetchMock, SEARCH).length
    const source = FakeEventSource.current()
    act(() => {
      source?.open()
      source?.send('folder_changed', { account: HOME, folder: 'INBOX', unread: 1, total: 2 })
    })
    await waitFor(() => {
      expect(bodiesTo(fetchMock, SEARCH).length).toBeGreaterThan(read)
    })
  })

  it('starts from the terms of a view that is a search, in its account and folder', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail?account=1&view=5')

    expect(await screen.findByText('Searching in: Archive · home')).toBeInTheDocument()
    expect(box()).toHaveValue('is:unread')
    expect(bodiesTo(fetchMock, SEARCH)).toEqual([])
    await userEvent.type(box(), ' invoice{Enter}')
    await results()
    expect(bodiesTo(fetchMock, SEARCH)).toEqual([{ account: HOME, folder: 'Archive', text: 'invoice', unread: true }])
    expect(screen.getByText('Searching in: Archive · home')).toBeInTheDocument()
  })

  it.each([6, 9])('is all mail, said so, for a view that no longer reads or is gone (%i)', async (view) => {
    const fetchMock = stubMailApi(state())
    renderMailApp(`/mail?account=1&view=${String(view)}`)

    expect(await screen.findByText('Searching in: All mail (not Junk or Trash)')).toBeInTheDocument()
    await userEvent.type(box(), 'invoice{Enter}')
    await results()
    expect(bodiesTo(fetchMock, SEARCH)).toEqual([{ account: '*', folder: '*', text: 'invoice' }])
  })

  it('waits for the saved views before searching in one', async () => {
    let release: () => void = () => undefined
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    const fetchMock = stubMailApi(state({ hold: (request) => (request === 'GET /api/v1/mail/views' ? held : undefined) }))
    renderMailApp('/mail?account=1&view=4')
    await screen.findByText('A list post')

    await userEvent.type(box(), 'invoice{Enter}')
    expect(screen.queryByText(/Searching in/)).not.toBeInTheDocument()
    expect(bodiesTo(fetchMock, SEARCH)).toEqual([])
    expect(queriesTo(fetchMock, '/api/v1/mail/views/4/messages')).toHaveLength(1)
    act(() => {
      release()
    })
    expect(await screen.findByText('Searching in: Lists')).toBeInTheDocument()
    expect(box()).toHaveValue('')
  })
})

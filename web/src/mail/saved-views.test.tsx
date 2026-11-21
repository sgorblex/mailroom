import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SavedView } from '@/api/mail.ts'
import { FakeEventSource, stubEventSource } from '@/test/fake-event-source.ts'
import { bodiesTo, HOME, messageItem, queriesTo, stubMailApi, WORK, type FakeMailState } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const LISTS: SavedView = { id: 4, name: 'Lists', sources: [{ account_id: WORK, folder: 'Lists' }], query: null, position: 0 }
const UNREAD: SavedView = { id: 5, name: 'Unread from Ada', sources: null, query: 'from:ada is:unread', position: 1 }

function state(fields: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 1, total: 1 },
        { path: 'Lists', role: null, unread: 4, total: 4 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 1 }],
    },
    messages: {
      '1/INBOX': [messageItem(WORK, 'INBOX', 3, { subject: 'From work' })],
      '1/Lists': [messageItem(WORK, 'Lists', 1, { subject: 'A list post' })],
      '2/INBOX': [messageItem(HOME, 'INBOX', 2, { subject: 'From home' })],
    },
    views: {},
    savedViews: [{ ...LISTS }, { ...UNREAD }],
    ...fields,
  }
}

const views = (): HTMLElement => screen.getByRole('list', { name: 'Views' })
const viewNames = (): string[] => within(views()).getAllByRole('link').map((link) => link.textContent)

async function openMenu(name: string): Promise<HTMLElement> {
  await userEvent.click(await within(views()).findByRole('button', { name: `Actions for ${name}` }))
  return screen.getByRole('menu')
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the views in the sidebar', () => {
  it('list All inboxes and the saved views in order, with the unread count of folders already listed', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    await screen.findByRole('list', { name: 'Messages' })
    await waitFor(() => {
      expect(viewNames()).toEqual(['All inboxes', 'Lists4', 'Unread from Ada'])
    })
    // HOME's folders are not listed yet: All inboxes says no count rather than a wrong one.
    expect(within(views()).getByRole('link', { name: 'All inboxes' })).toBeInTheDocument()
    expect(within(views()).getByRole('link', { name: 'Lists, 4 unread' })).toBeInTheDocument()
  })

  it("open a view's list across accounts, with its rows' accounts, the folder no longer current", async () => {
    const listed = state()
    listed.folders[WORK]?.push({ path: 'Archive', role: 'archive', unread: 0, total: 0 })
    const fetchMock = stubMailApi(listed)
    renderMailApp('/mail')

    await screen.findByRole('list', { name: 'Messages' })
    await userEvent.click(within(views()).getByRole('link', { name: 'All inboxes' }))

    const list = await screen.findByRole('list', { name: 'Messages' })
    await within(list).findByRole('link', { name: /From home/ })
    expect(within(list).getAllByRole('listitem').map((row) => /From (work|home)/.exec(row.textContent)?.[0])).toEqual(['From work', 'From home'])
    expect(within(list).getAllByText('home').length).toBeGreaterThan(0)
    expect(queriesTo(fetchMock, '/api/v1/mail/views/all_inboxes/messages')).toHaveLength(1)
    expect(within(views()).getByRole('link', { name: 'All inboxes' })).toHaveAttribute('aria-current', 'page')
    const folders = screen.getByRole('tree', { name: 'Folders of work' })
    expect(within(folders).getByRole('treeitem', { name: /Inbox/ })).not.toHaveAttribute('aria-current')
    expect(screen.queryByRole('group', { name: 'Show only' })).not.toBeInTheDocument()

    // The row's actions act on it as anywhere else.
    const home = within(list).getByRole('link', { name: /From home/ }).closest('li')
    expect(home).not.toBeNull()
    await userEvent.click(within(home as HTMLElement).getByRole('button', { name: 'Star' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toEqual([{ refs: ['ref-2-INBOX-2'], flagged: true }])
    })
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select From work' }))
    expect(within(list).getByRole('checkbox', { name: 'Select From work' })).toBeChecked()
    // Acted on from INBOX, where a Gmail row (listed from All Mail) is then archived.
    await userEvent.keyboard('e')
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/archive')).toEqual([{ refs: ['ref-1-INBOX-3'], source_folder: 'INBOX' }])
    })
  })

  it('act on each Gmail row of a view of different folders in the folder it was listed under', async () => {
    const ALL_MAIL = '[Gmail]/All Mail'
    const mixed: SavedView = {
      id: 6,
      name: 'Mixed',
      sources: [
        { account_id: WORK, folder: 'INBOX' },
        { account_id: WORK, folder: 'Work' },
      ],
      query: null,
      position: 2,
    }
    const fetchMock = stubMailApi(
      state({
        savedViews: [mixed],
        viewPages: {
          '6': {
            messages: [
              messageItem(WORK, ALL_MAIL, 8, { subject: 'Labelled Work', listed_folder: 'Work' }),
              messageItem(WORK, ALL_MAIL, 7, { subject: 'In the inbox', listed_folder: 'INBOX' }),
            ],
            next_cursor: null,
            failures: [],
            not_indexed: [],
          },
        },
      }),
    )
    renderMailApp('/mail?view=6')

    const list = await screen.findByRole('list', { name: 'Messages' })
    await userEvent.click(await within(list).findByRole('checkbox', { name: 'Select Labelled Work' }))
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select In the inbox' }))
    await userEvent.keyboard('#')
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/trash')).toEqual([
        {
          refs: [`ref-1-${ALL_MAIL}-8`, `ref-1-${ALL_MAIL}-7`],
          source_folders: { [`ref-1-${ALL_MAIL}-8`]: 'Work', [`ref-1-${ALL_MAIL}-7`]: 'INBOX' },
        },
      ])
    })
  })

  it('name the folders a view leaves out, not indexed yet', async () => {
    stubMailApi(
      state({
        viewPages: {
          '4': { messages: [], next_cursor: null, failures: [], not_indexed: [{ account_id: WORK, folder: 'Lists' }, { account_id: HOME, folder: '*' }] },
        },
      }),
    )
    renderMailApp('/mail?view=4')

    expect(await screen.findByText('Left out, as they are not indexed yet: work: Lists; home: every folder.')).toBeInTheDocument()
    expect(screen.getByText('No messages in this view.')).toBeInTheDocument()
  })

  it('read the list again when one of its folders changes', async () => {
    stubEventSource()
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail?view=4')

    await screen.findByRole('link', { name: /A list post/ })
    const source = await waitFor(() => {
      const current = FakeEventSource.current()
      if (current === undefined) {
        throw new Error('no stream open')
      }
      return current
    })
    act(() => {
      source.open()
      source.send('folder_changed', { account: HOME, folder: 'INBOX', unread: 1, total: 2 })
    })
    act(() => {
      source.send('folder_changed', { account: WORK, folder: 'Lists', unread: 5, total: 5 })
    })
    await waitFor(() => {
      expect(queriesTo(fetchMock, '/api/v1/mail/views/4/messages')).toHaveLength(2)
    })
  })

  it('rename, reorder and delete a view, leaving it when it is shown', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail?view=4')

    await screen.findByRole('link', { name: /A list post/ })
    await userEvent.click(within(await openMenu('Lists')).getByRole('menuitem', { name: 'Rename…' }))
    let dialog = screen.getByRole('dialog', { name: 'Rename “Lists”' })
    const name = within(dialog).getByRole('textbox', { name: 'Name' })
    await userEvent.clear(name)
    await userEvent.type(name, 'Unread from Ada')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Rename' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('You already have a view with that name.')
    await userEvent.clear(name)
    await userEvent.type(name, '  Mailing lists ')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Rename' }))
    await waitFor(() => {
      expect(viewNames()).toEqual(['All inboxes', 'Mailing lists4', 'Unread from Ada'])
    })
    expect(bodiesTo(fetchMock, '/api/v1/mail/views/4').at(-1)).toEqual({ name: 'Mailing lists' })

    await userEvent.click(within(await openMenu('Mailing lists')).getByRole('menuitem', { name: 'Move down' }))
    await waitFor(() => {
      expect(viewNames()).toEqual(['All inboxes', 'Unread from Ada', 'Mailing lists4'])
    })
    expect(bodiesTo(fetchMock, '/api/v1/mail/views/order')).toEqual([{ view_ids: [5, 4] }])
    const menu = await openMenu('Mailing lists')
    expect(within(menu).getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Rename…', 'Move up', 'Delete…'])

    await userEvent.click(within(menu).getByRole('menuitem', { name: 'Delete…' }))
    dialog = screen.getByRole('dialog', { name: 'Delete the view “Mailing lists”' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))
    await waitFor(() => {
      expect(viewNames()).toEqual(['All inboxes', 'Unread from Ada'])
    })
    expect(await screen.findByRole('link', { name: /From work/ })).toBeInTheDocument()
  })

  it('say a move that failed, keeping the order', async () => {
    stubMailApi(state({ refuse: { 'PUT /api/v1/mail/views/order': { status: 500, body: { detail: 'Internal Server Error' } } } }))
    renderMailApp('/mail?view=4')

    await screen.findByRole('link', { name: /A list post/ })
    await userEvent.click(within(await openMenu('Lists')).getByRole('menuitem', { name: 'Move down' }))
    expect(await screen.findByText('Could not change the view.')).toBeInTheDocument()
    expect(viewNames()).toEqual(['All inboxes', 'Lists4', 'Unread from Ada'])
  })

  it('say a view opened from the URL is gone', async () => {
    stubMailApi(state())
    renderMailApp('/mail?view=99')

    expect(await screen.findByText('This view no longer exists, or names an account that is gone.')).toBeInTheDocument()
  })
})

describe('Save as view', () => {
  it('keeps the search shown as a view, named, and opens it', async () => {
    const fetchMock = stubMailApi(
      state({ savedViews: [], searchResult: { messages: [messageItem(WORK, 'INBOX', 3, { subject: 'From Ada' })], next_cursor: null, failures: [] } }),
    )
    renderMailApp(`/mail?q=${encodeURIComponent(JSON.stringify({ account: '1', folder: 'INBOX', from: 'ada' }))}`)

    await screen.findByRole('list', { name: 'Search results' })
    await userEvent.click(screen.getByRole('button', { name: 'Save as view' }))
    const dialog = screen.getByRole('dialog', { name: 'Save this search as a view' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(within(dialog).getByText('Give the view a name.')).toBeInTheDocument()
    await userEvent.type(within(dialog).getByRole('textbox', { name: 'Name' }), 'Ada')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(within(views()).getByRole('link', { name: 'Ada' })).toHaveAttribute('aria-current', 'page')
    })
    expect(bodiesTo(fetchMock, '/api/v1/mail/views').filter(Boolean)).toEqual([{ name: 'Ada', query: 'from:ada in:INBOX account:1' }])
    expect(screen.getByRole('searchbox', { name: 'Search terms' })).toHaveValue('')
  })

  it('keeps the quick filters on as terms of the line saved', async () => {
    const fetchMock = stubMailApi(state({ savedViews: [] }))
    renderMailApp(`/mail?q=${encodeURIComponent(JSON.stringify({ account: '1', folder: 'INBOX', from: 'ada' }))}&only=%5B%22unread%22%5D`)

    await screen.findByText('No message matches.')
    await userEvent.click(screen.getByRole('button', { name: 'Save as view' }))
    const dialog = screen.getByRole('dialog', { name: 'Save this search as a view' })
    await userEvent.type(within(dialog).getByRole('textbox', { name: 'Name' }), 'Unread from Ada')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/views').filter(Boolean)).toEqual([
        { name: 'Unread from Ada', query: 'from:ada is:unread in:INBOX account:1' },
      ])
    })
  })

  it('says a name already taken, keeping the dialog open', async () => {
    stubMailApi(state())
    renderMailApp(`/mail?q=${encodeURIComponent(JSON.stringify({ account: '1', folder: 'INBOX', from: 'ada' }))}`)

    await userEvent.click(await screen.findByRole('button', { name: 'Save as view' }))
    const dialog = screen.getByRole('dialog', { name: 'Save this search as a view' })
    await userEvent.type(within(dialog).getByRole('textbox', { name: 'Name' }), 'Lists')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('You already have a view with that name.')
    expect(screen.getByRole('dialog', { name: 'Save this search as a view' })).toBeInTheDocument()
  })

  it('is not offered for a search of words, which the index cannot answer', async () => {
    stubMailApi(state())
    renderMailApp(`/mail?q=${encodeURIComponent(JSON.stringify({ account: '1', text: 'invoice' }))}`)

    const button = await screen.findByRole('button', { name: 'Save as view' })
    expect(button).toHaveAttribute('aria-disabled', 'true')
    await userEvent.click(button)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

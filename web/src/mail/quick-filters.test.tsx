import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { FakeEventSource, stubEventSource } from '@/test/fake-event-source.ts'
import { bodiesTo, HOME, messageItem, messageView, stubMailApi, WORK, type FakeMailState } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

function state(found = true): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 1 },
        { path: 'Archive', role: 'archive', unread: 0, total: 1 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 1 }],
    },
    messages: {
      '1/INBOX': [messageItem(WORK, 'INBOX', 1, { subject: 'From work' })],
      '1/Archive': [messageItem(WORK, 'Archive', 1, { subject: 'Archived' })],
      '2/INBOX': [messageItem(HOME, 'INBOX', 1, { subject: 'From home' })],
    },
    views: {},
    searchResult: {
      messages: found ? [messageItem(WORK, 'INBOX', 1, { subject: 'Found' })] : [],
      next_cursor: null,
      failures: [],
    },
  }
}

const filters = (): HTMLElement => screen.getByRole('group', { name: 'Show only' })
const toggle = (name: string): HTMLElement => within(filters()).getByRole('button', { name })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the quick filters', () => {
  it('narrow the folder shown, which stays the one open, and let go of it again', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    await screen.findByRole('list', { name: 'Messages' })
    expect(toggle('Unread')).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(toggle('Unread'))

    expect(await screen.findByRole('link', { name: /Found/ })).toBeInTheDocument()
    expect(toggle('Unread')).toHaveAttribute('aria-pressed', 'true')
    expect(bodiesTo(fetchMock, '/api/v1/mail/search')).toEqual([{ account: 1, folder: 'INBOX', unread: true }])
    // Still the folder's list: its rows name no folder, the sidebar keeps it, the search box stays empty.
    expect(screen.getByRole('list', { name: 'Messages' })).toBeInTheDocument()
    const folders = screen.getByRole('tree', { name: 'Folders of work' })
    expect(within(folders).getByRole('treeitem', { name: 'Inbox' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('searchbox', { name: 'Search terms' })).toHaveValue('')

    await userEvent.click(toggle('Unread'))
    expect(await screen.findByRole('link', { name: /From work/ })).toBeInTheDocument()
    expect(toggle('Unread')).toHaveAttribute('aria-pressed', 'false')
  })

  it('search a folder listed with its subfolders with its subfolders', async () => {
    const listed = state()
    listed.folders[WORK] = [
      { path: 'INBOX', role: 'inbox', unread: 0, total: 1 },
      { path: 'INBOX/Lists', role: null, unread: 0, total: 1 },
    ]
    listed.messages['1/INBOX/Lists'] = [messageItem(WORK, 'INBOX/Lists', 2, { subject: 'Release notes' })]
    const fetchMock = stubMailApi(listed)
    renderMailApp('/mail?account=1&folder=INBOX&sub=true')

    expect(await screen.findByRole('link', { name: /Release notes/ })).toBeInTheDocument()
    await userEvent.click(toggle('Unread'))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/search')).toEqual([{ account: 1, folder: 'INBOX', unread: true, subfolders: true }])
    })
  })

  it('add their terms to a search, and a new source starts without them', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail?q=%7B%22account%22%3A%22*%22%2C%22from%22%3A%22ada%22%7D')

    await screen.findByRole('list', { name: 'Search results' })
    await userEvent.click(toggle('Starred'))
    await userEvent.click(toggle('With attachments'))

    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/search').at(-1)).toEqual({
        account: '*',
        from: 'ada',
        flagged: true,
        has_attachment: true,
      })
    })
    // The search itself is unchanged: the line shows only its own terms.
    expect(screen.getByRole('searchbox', { name: 'Search terms' })).toHaveValue('from:ada account:*')

    await userEvent.click(within(screen.getByRole('tree', { name: 'Folders of work' })).getByRole('treeitem', { name: 'Archive' }))
    expect(await screen.findByRole('link', { name: /Archived/ })).toBeInTheDocument()
    expect(toggle('Starred')).toHaveAttribute('aria-pressed', 'false')
    expect(toggle('With attachments')).toHaveAttribute('aria-pressed', 'false')
  })

  it('are kept in the URL, toggled from the keyboard, and say when nothing matches', async () => {
    const fetchMock = stubMailApi(state(false))
    renderMailApp('/mail?only=%5B%22flagged%22%5D')

    expect(await screen.findByText('No message matches.')).toBeInTheDocument()
    expect(toggle('Starred')).toHaveAttribute('aria-pressed', 'true')
    expect(bodiesTo(fetchMock, '/api/v1/mail/search')).toEqual([{ account: 1, folder: 'INBOX', flagged: true }])

    toggle('Unread').focus()
    await userEvent.keyboard('{Enter}')
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/search').at(-1)).toEqual({
        account: 1,
        folder: 'INBOX',
        unread: true,
        flagged: true,
      })
    })
    expect(toggle('Unread')).toHaveAttribute('aria-pressed', 'true')
  })

  it('keep a row this screen changed so it no longer matches, read again too, and j goes on from it', async () => {
    stubEventSource()
    const unreadFlags = { seen: false, flagged: false, answered: false, forwarded: false, draft: false, to_sort: false }
    const unread = [3, 2, 1].map((uid) => messageItem(WORK, 'INBOX', uid, { flags: unreadFlags }))
    const [, second, first] = unread
    const fake = state()
    fake.messages['1/INBOX'] = [...unread]
    fake.views = Object.fromEntries(unread.map((item) => [item.ref, messageView(item)]))
    fake.searchResult = { messages: [...unread], next_cursor: null, failures: [] }
    const fetchMock = stubMailApi(fake)
    const history = renderMailApp('/mail?only=%5B%22unread%22%5D')

    const list = await screen.findByRole('list', { name: 'Messages' })
    await userEvent.click(within(list).getByRole('link', { name: /Message 2/ }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toEqual([{ refs: [second?.ref], seen: true }])
    })

    // Read now, it no longer matches; new mail comes in and the list is read again without it.
    const source = await waitFor(() => {
      const current = FakeEventSource.current()
      if (current === undefined) {
        throw new Error('no stream open')
      }
      return current
    })
    act(() => {
      source.open()
    })
    const reads = bodiesTo(fetchMock, '/api/v1/mail/search').length
    fake.searchResult = {
      messages: [messageItem(WORK, 'INBOX', 4, { flags: unreadFlags }), ...unread.filter((item) => item !== second)],
      next_cursor: null,
      failures: [],
    }
    act(() => {
      source.send('new_mail', { account: WORK, folder: 'INBOX', count: 1, messages: [] })
    })
    await within(list).findByText('Message 4')
    expect(bodiesTo(fetchMock, '/api/v1/mail/search').length).toBeGreaterThan(reads)
    expect(within(list).getAllByRole('listitem').map((row) => /Message \d/.exec(row.textContent)?.[0])).toEqual([
      'Message 4',
      'Message 3',
      'Message 2',
      'Message 1',
    ])
    expect(within(list).getByRole('link', { name: /Message 2/ })).toHaveAttribute('aria-current', 'page')

    await userEvent.keyboard('j')
    await waitFor(() => {
      expect(history.location.search).toContain(`message=${String(first?.ref)}`)
    })
  })
})

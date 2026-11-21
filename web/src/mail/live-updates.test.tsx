import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { FakeEventSource, stubEventSource } from '@/test/fake-event-source.ts'
import { HOME, indexFolder, messageItem, messageView, queriesTo, stubMailApi, WORK, type FakeMailState } from '@/test/fake-mail-api.ts'
import { listView, scrollListToEnd } from '@/test/fake-virtualizer.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const inbox = Array.from({ length: 30 }, (_, index) => messageItem(WORK, 'INBOX', 100 - index))

function state(fields: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 30 },
        { path: 'Archive', role: 'archive', unread: 0, total: 1 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 0 }],
    },
    messages: { '1/INBOX': [...inbox], '1/Archive': [messageItem(WORK, 'Archive', 1, { subject: 'Filed' })], '2/INBOX': [] },
    views: Object.fromEntries(inbox.map((item) => [item.ref, messageView(item)])),
    ...fields,
  }
}

/** The stream open now, opened. */
async function opened(): Promise<FakeEventSource> {
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
  return source
}

function send(source: FakeEventSource, type: string, data: unknown): void {
  act(() => {
    source.send(type, data)
  })
}

const messagesList = (): Promise<HTMLElement> => screen.findByRole('list', { name: 'Messages' })

const subjects = (list: HTMLElement): string[] =>
  within(list)
    .getAllByRole('listitem')
    .map((row) => /Message \d+/.exec(row.textContent)?.[0] ?? '')

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('live updates', () => {
  it('opens one stream with the folder shown, again when it changes, and with none for a search', async () => {
    const sources = stubEventSource()
    stubMailApi(state())
    const history = renderMailApp('/mail')

    await messagesList()
    await waitFor(() => {
      expect(FakeEventSource.current()?.url).toBe('/api/v1/mail/events?account=1&folder=INBOX')
    })
    expect(FakeEventSource.current()?.withCredentials).toBe(true)

    act(() => {
      history.push('/mail?account=1&folder=Archive')
    })
    await screen.findByText('Filed')
    await waitFor(() => {
      expect(FakeEventSource.current()?.url).toBe('/api/v1/mail/events?account=1&folder=Archive')
    })

    act(() => {
      history.push(`/mail?q=${encodeURIComponent(JSON.stringify({ from: 'someone' }))}`)
    })
    await waitFor(() => {
      expect(FakeEventSource.current()?.url).toBe('/api/v1/mail/events')
    })
    expect(sources.instances.filter((source) => source.readyState !== FakeEventSource.CLOSED)).toHaveLength(1)
  })

  it("sets a folder's counts from the event, without reading the folders again", async () => {
    stubEventSource()
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    await messagesList()
    const source = await opened()
    const reads = queriesTo(fetchMock, '/api/v1/mail/accounts/1/folders').length
    send(source, 'folder_changed', { account: WORK, folder: 'Archive', unread: 4, total: 5 })
    expect(await screen.findByRole('treeitem', { name: 'Archive, 4 unread' })).toBeInTheDocument()
    expect(queriesTo(fetchMock, '/api/v1/mail/accounts/1/folders')).toHaveLength(reads)
  })

  it('brings new mail in at the top, keeping the rows loaded below, the selection and the open message', async () => {
    stubEventSource()
    const fake = state()
    stubMailApi(fake)
    renderMailApp(`/mail?message=${inbox[3]?.ref ?? ''}`)

    const list = await messagesList()
    scrollListToEnd()
    await within(list).findByText('Message 71')
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 99' }))
    const source = await opened()

    fake.messages['1/INBOX'] = [messageItem(WORK, 'INBOX', 102), messageItem(WORK, 'INBOX', 101), ...inbox]
    send(source, 'new_mail', { account: WORK, folder: 'INBOX', count: 2, messages: [] })
    await within(list).findByText('Message 102')
    expect(subjects(list)).toEqual(['Message 102', 'Message 101', ...inbox.map((item) => item.subject)])
    expect(within(list).getByRole('checkbox', { name: 'Select Message 99' })).toBeChecked()
    expect(within(list).getByRole('link', { name: /Message 97/ })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('article')).toHaveTextContent('Body of Message 97')
  })

  it('keeps the rows in view where they are as new mail comes in above them, but at the very top', async () => {
    stubEventSource()
    const fake = state()
    stubMailApi(fake)
    renderMailApp('/mail')

    const list = await messagesList()
    const source = await opened()
    fake.messages['1/INBOX'] = [messageItem(WORK, 'INBOX', 101), ...inbox]
    send(source, 'new_mail', { account: WORK, folder: 'INBOX', count: 1, messages: [] })
    await within(list).findByText('Message 101')
    expect(listView()?.startIndex).toBe(0)

    scrollListToEnd()
    await within(list).findByText('Message 71')
    const top = listView()?.startIndex ?? 0
    fake.messages['1/INBOX'] = [messageItem(WORK, 'INBOX', 103), messageItem(WORK, 'INBOX', 102), ...(fake.messages['1/INBOX'] ?? [])]
    send(source, 'new_mail', { account: WORK, folder: 'INBOX', count: 2, messages: [] })
    await within(list).findByText('Message 103')
    expect(listView()?.startIndex).toBe(top + 2)
  })

  it('brings the open row into view, and follows it as j moves on', async () => {
    stubEventSource()
    stubMailApi(state())
    renderMailApp(`/mail?message=${inbox[20]?.ref ?? ''}`)

    await messagesList()
    await waitFor(() => {
      expect(listView()?.endIndex).toBe(20)
    })
    await userEvent.keyboard('j')
    await waitFor(() => {
      expect(listView()?.endIndex).toBe(21)
    })
  })

  it('drops the rows gone from the first page, and keeps those new mail pushed below it', async () => {
    stubEventSource()
    const fake = state()
    stubMailApi(fake)
    renderMailApp('/mail')

    const list = await messagesList()
    scrollListToEnd()
    await within(list).findByText('Message 71')
    const source = await opened()

    // 99 expunged elsewhere; 101 arrived, pushing 76 off the first page.
    fake.messages['1/INBOX'] = [messageItem(WORK, 'INBOX', 101), ...inbox.filter((item) => item.subject !== 'Message 99')]
    send(source, 'folder_changed', { account: WORK, folder: 'INBOX', unread: 0, total: 30 })
    await within(list).findByText('Message 101')
    expect(subjects(list)).toEqual(['Message 101', ...inbox.filter((item) => item.subject !== 'Message 99').map((item) => item.subject)])
  })

  it('drops a page loaded below while the first page was read again', async () => {
    stubEventSource()
    let release: (() => void) | undefined
    let holdNext = false
    const fake = state({
      hold: (request) => {
        if (!holdNext || request !== 'GET /api/v1/mail/accounts/1/messages') {
          return undefined
        }
        holdNext = false
        return new Promise<void>((resolve) => {
          release = resolve
        })
      },
    })
    stubMailApi(fake)
    renderMailApp('/mail')

    const list = await messagesList()
    const source = await opened()
    holdNext = true
    scrollListToEnd()
    await waitFor(() => {
      expect(release).toBeDefined()
    })
    // 99 expunged elsewhere while the next page is read: the first page now reaches one row further.
    const kept = inbox.filter((item) => item.subject !== 'Message 99')
    fake.messages['1/INBOX'] = kept
    send(source, 'folder_changed', { account: WORK, folder: 'INBOX', unread: 0, total: 29 })
    await within(list).findByText('Message 75')
    await act(async () => {
      release?.()
      await Promise.resolve()
    })
    // The stale page is dropped, and the next one read from where the first page now ends: no row twice.
    await waitFor(() => {
      expect(subjects(list)).toEqual(kept.map((item) => item.subject))
    })
  })

  it("shows flags changed elsewhere on the rows and the open message's toolbar", async () => {
    stubEventSource()
    const fake = state()
    stubMailApi(fake)
    const open = inbox[0]
    renderMailApp(`/mail?message=${open?.ref ?? ''}`)

    const list = await messagesList()
    await screen.findByText('Body of Message 100')
    const source = await opened()
    expect(await screen.findByRole('button', { name: 'Mark as unread' })).toBeInTheDocument()

    fake.messages['1/INBOX'] = inbox.map((item) =>
      item === open ? { ...item, flags: { ...item.flags, seen: false, flagged: true } } : item,
    )
    send(source, 'folder_changed', { account: WORK, folder: 'INBOX', unread: 1, total: 30 })
    await waitFor(() => {
      const [row] = within(list).getAllByRole('listitem')
      expect(row && within(row).getByRole('button', { name: 'Star' })).toHaveAttribute('aria-pressed', 'true')
    })
    expect(await screen.findByRole('button', { name: 'Mark as read' })).toBeInTheDocument()
  })

  it("leaves the list alone for another folder's change", async () => {
    stubEventSource()
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    await messagesList()
    const source = await opened()
    const reads = queriesTo(fetchMock, '/api/v1/mail/accounts/1/messages').length
    send(source, 'folder_changed', { account: WORK, folder: 'Archive', unread: 0, total: 2 })
    send(source, 'folder_changed', { account: HOME, folder: 'INBOX', unread: 0, total: 1 })
    await screen.findByRole('treeitem', { name: 'Archive' })
    expect(queriesTo(fetchMock, '/api/v1/mail/accounts/1/messages')).toHaveLength(reads)
  })

  it('moves the indexing line on with the sync, and takes it away once ready', async () => {
    stubEventSource()
    stubMailApi(
      state({
        index: { [WORK]: { folders: [indexFolder('INBOX', { role: 'inbox', enabled: true, state: 'syncing', total: 8000, indexed: 1240 })] } },
      }),
    )
    renderMailApp('/mail')

    await screen.findByText('Indexing: 1,240 of 8,000 messages')
    const source = await opened()
    send(source, 'sync', { account: WORK, folder: 'INBOX', state: 'syncing', progress: 2500 })
    expect(await screen.findByText('Indexing: 2,500 of 8,000 messages')).toBeInTheDocument()
    send(source, 'sync', { account: WORK, folder: 'INBOX', state: 'ready', progress: 8000 })
    await waitFor(() => {
      expect(screen.queryByText(/^Indexing/)).not.toBeInTheDocument()
    })
  })

  it('says it is reconnecting after a failure, opens again after a wait, and reads the list again', async () => {
    const sources = stubEventSource()
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    await messagesList()
    const first = await opened()
    const made = sources.instances.length
    act(() => {
      first.fail()
    })
    expect(await screen.findByText('Reconnecting to live updates…')).toBeInTheDocument()
    expect(first.readyState).toBe(FakeEventSource.CLOSED)
    const reads = queriesTo(fetchMock, '/api/v1/mail/accounts/1/messages').length

    await waitFor(
      () => {
        expect(sources.instances).toHaveLength(made + 1)
      },
      { timeout: 2000 },
    )
    await opened()
    await waitFor(() => {
      expect(screen.queryByText('Reconnecting to live updates…')).not.toBeInTheDocument()
    })
    await waitFor(() => {
      expect(queriesTo(fetchMock, '/api/v1/mail/accounts/1/messages').length).toBeGreaterThan(reads)
    })
  })

  it("reads the folders' counts again once opened for another folder", async () => {
    stubEventSource()
    const fetchMock = stubMailApi(state())
    const history = renderMailApp('/mail')

    await messagesList()
    await opened()
    act(() => {
      history.push('/mail?account=1&folder=Archive')
    })
    await screen.findByText('Filed')
    await waitFor(() => {
      expect(FakeEventSource.current()?.url).toBe('/api/v1/mail/events?account=1&folder=Archive')
    })
    const reads = queriesTo(fetchMock, '/api/v1/mail/accounts/1/folders').length
    await opened()
    await waitFor(() => {
      expect(queriesTo(fetchMock, '/api/v1/mail/accounts/1/folders').length).toBeGreaterThan(reads)
    })
  })

  it('ignores an event whose data is not what its type says', async () => {
    stubEventSource()
    stubMailApi(state())
    renderMailApp('/mail')

    await messagesList()
    const source = await opened()
    send(source, 'folder_changed', { account: WORK, folder: 'Archive', unread: 'many' })
    act(() => {
      source.dispatchEvent(new MessageEvent('folder_changed', { data: 'not json' }))
    })
    expect(screen.getByRole('treeitem', { name: 'Archive' })).toBeInTheDocument()
  })
})

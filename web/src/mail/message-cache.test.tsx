import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { FakeEventSource, stubEventSource } from '@/test/fake-event-source.ts'
import { bodiesTo, type FakeMailState, messageItem, messageView, queriesTo, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const unread = messageItem(WORK, 'INBOX', 100, {
  subject: 'Unread one',
  flags: { seen: false, flagged: false, answered: false, forwarded: false, draft: false, to_sort: false },
})
const first = messageItem(WORK, 'INBOX', 99, { subject: 'First one' })
const second = messageItem(WORK, 'INBOX', 98, { subject: 'Second one' })
const third = messageItem(WORK, 'INBOX', 97, { subject: 'Third one' })
const archived = messageItem(WORK, 'Archive', 7, { subject: 'Old news' })

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 1, total: 4 },
        { path: 'Archive', role: 'archive', unread: 0, total: 1 },
      ],
    },
    messages: { '1/INBOX': [unread, first, second, third], '1/Archive': [archived] },
    views: Object.fromEntries([unread, first, second, third, archived].map((item) => [item.ref, messageView(item)])),
    ...overrides,
  }
}

const messagesList = (): Promise<HTMLElement> => screen.findByRole('list', { name: 'Messages' })
const readsOf = (fetchMock: ReturnType<typeof stubMailApi>, ref: string): number =>
  queriesTo(fetchMock, `/api/v1/mail/messages/${ref}`).length
const reader = (): HTMLElement => screen.getByRole('region', { name: 'Message' })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('reading messages through the cache', () => {
  it('keeps the message on show, dimmed and busy, while the next one loads', async () => {
    let release = (): void => undefined
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    stubMailApi(state({ hold: (request) => (request === `GET /api/v1/mail/messages/${second.ref}` ? held : undefined) }))
    renderMailApp(`/mail?message=${first.ref}`)
    expect(await screen.findByText(`Body of ${first.subject}`)).toBeInTheDocument()

    await userEvent.click(within(await messagesList()).getByRole('link', { name: /Second one/ }))
    expect(within(reader()).getByText(`Body of ${first.subject}`)).toBeInTheDocument()
    const busy = within(reader()).getByText(`Body of ${first.subject}`).closest('[aria-busy="true"]')
    expect(busy).not.toBeNull()
    expect(busy).toHaveAttribute('inert')
    expect(within(reader()).getByRole('status')).toHaveTextContent('Loading the message…')

    act(() => {
      release()
    })
    expect(await screen.findByText(`Body of ${second.subject}`)).toBeInTheDocument()
    expect(screen.queryByText(`Body of ${first.subject}`)).not.toBeInTheDocument()
    expect(reader().querySelector('[aria-busy="true"]')).toBeNull()
  })

  it('shows a message opened again from the cache, without reading it again', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    await userEvent.click(within(await messagesList()).getByRole('link', { name: /First one/ }))
    expect(await screen.findByText(`Body of ${first.subject}`)).toBeInTheDocument()
    await userEvent.click(within(await messagesList()).getByRole('link', { name: /Second one/ }))
    expect(await screen.findByText(`Body of ${second.subject}`)).toBeInTheDocument()
    await userEvent.click(within(await messagesList()).getByRole('link', { name: /First one/ }))

    expect(screen.getByText(`Body of ${first.subject}`)).toBeInTheDocument()
    expect(readsOf(fetchMock, first.ref)).toBe(1)
  })

  it('opens the first message from the read its press started, read once', async () => {
    let release = (): void => undefined
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    const fetchMock = stubMailApi(state({ hold: (request) => (request === `GET /api/v1/mail/messages/${first.ref}` ? held : undefined) }))
    // As main.tsx renders it: the reading view mounted, unmounted and mounted again.
    renderMailApp('/mail', { strict: true })

    await userEvent.click(within(await messagesList()).getByRole('link', { name: /First one/ }))
    act(() => {
      release()
    })
    expect(await screen.findByText(`Body of ${first.subject}`)).toBeInTheDocument()
    expect(readsOf(fetchMock, first.ref)).toBe(1)
  })

  it('writes the mark-read into the cached read, so opening it again does not mark it again', async () => {
    const fetchMock = stubMailApi(state())
    const history = renderMailApp('/mail')

    await userEvent.click(within(await messagesList()).getByRole('link', { name: /Unread one/ }))
    expect(await screen.findByText(`Body of ${unread.subject}`)).toBeInTheDocument()
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toHaveLength(1)
    })

    // Another list, whose flags start afresh, and the message opened there from its link.
    act(() => {
      history.push('/mail?account=1&folder=Archive')
    })
    await screen.findByText('Old news')
    act(() => {
      history.push(`/mail?account=1&folder=Archive&message=${unread.ref}`)
    })
    expect(await screen.findByText(`Body of ${unread.subject}`)).toBeInTheDocument()

    expect(readsOf(fetchMock, unread.ref)).toBe(1)
    // Let a mark-read that should not happen have its chance.
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toHaveLength(1)
  })

  it('forgets the cached read of a message moved away', async () => {
    const fetchMock = stubMailApi(state())
    const history = renderMailApp(`/mail?message=${first.ref}`)
    expect(await screen.findByText(`Body of ${first.subject}`)).toBeInTheDocument()

    await userEvent.click(await screen.findByRole('button', { name: 'Archive' }))
    await waitFor(() => {
      expect(screen.queryByText(`Body of ${first.subject}`)).not.toBeInTheDocument()
    })
    // Its old ref followed back: read again, not shown from what was kept.
    act(() => {
      history.push(`/mail?message=${first.ref}`)
    })
    await waitFor(() => {
      expect(readsOf(fetchMock, first.ref)).toBe(2)
    })
  })

  it('reads a row ahead when the mouse rests on it, and the next row on j, marking neither read', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')
    const list = await messagesList()

    await userEvent.hover(within(list).getByRole('link', { name: /Unread one/ }))
    await waitFor(() => {
      expect(readsOf(fetchMock, unread.ref)).toBe(1)
    })

    await userEvent.click(within(list).getByRole('link', { name: /First one/ }))
    expect(await screen.findByText(`Body of ${first.subject}`)).toBeInTheDocument()
    await userEvent.keyboard('j')
    await waitFor(() => {
      expect(readsOf(fetchMock, third.ref)).toBe(1)
    })
    expect(await screen.findByText(`Body of ${second.subject}`)).toBeInTheDocument()

    // Opening the one read ahead reads it no more.
    await userEvent.click(within(list).getByRole('link', { name: /Unread one/ }))
    expect(await screen.findByText(`Body of ${unread.subject}`)).toBeInTheDocument()
    expect(readsOf(fetchMock, unread.ref)).toBe(1)
    // Only the message opened was marked read.
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toEqual([{ refs: [unread.ref], seen: true }])
    })
    expect(screen.queryByText(`Body of ${third.subject}`)).not.toBeInTheDocument()
  })

  it('shows the screen at once when coming back to Mail, reading the accounts again behind it', async () => {
    const fetchMock = stubMailApi(state())
    const history = renderMailApp('/mail')
    await messagesList()

    act(() => {
      history.push('/accounts')
    })
    await screen.findByText('Accounts page')
    act(() => {
      history.push('/mail')
    })
    expect(screen.queryByText('Loading your accounts…')).not.toBeInTheDocument()
    await waitFor(() => {
      expect(queriesTo(fetchMock, '/api/v1/accounts')).toHaveLength(2)
    })
  })

  it('shows the last list at once when coming back to Mail, reading it, the folders and the held sends once behind it', async () => {
    stubEventSource()
    let lists = 0
    let release = (): void => undefined
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    const fetchMock = stubMailApi(
      state({
        hold: (request) => {
          if (request === 'GET /api/v1/mail/accounts/1/messages') {
            lists += 1
            return lists === 2 ? held : undefined
          }
          return undefined
        },
      }),
    )
    const history = renderMailApp('/mail')
    await messagesList()
    act(() => {
      FakeEventSource.current()?.open()
    })
    await waitFor(() => {
      expect(queriesTo(fetchMock, '/api/v1/mail/scheduled')).toHaveLength(1)
    })
    const before = { folders: queriesTo(fetchMock, '/api/v1/mail/accounts/1/folders').length, scheduled: 1 }

    act(() => {
      history.push('/accounts')
    })
    await screen.findByText('Accounts page')
    act(() => {
      FakeEventSource.current()?.open()
    })
    act(() => {
      history.push('/mail')
    })
    // The rows as last read, while the list is read again.
    expect(within(await messagesList()).getByRole('link', { name: /First one/ })).toBeInTheDocument()
    expect(screen.queryByText('Loading messages…')).not.toBeInTheDocument()
    await waitFor(() => {
      expect(lists).toBe(2)
    })
    act(() => {
      FakeEventSource.current()?.open()
      release()
    })
    await waitFor(() => {
      expect(queriesTo(fetchMock, '/api/v1/accounts')).toHaveLength(2)
    })
    // Let a duplicate have its chance.
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(queriesTo(fetchMock, '/api/v1/mail/accounts/1/folders')).toHaveLength(before.folders + 1)
    expect(queriesTo(fetchMock, '/api/v1/mail/scheduled')).toHaveLength(before.scheduled)
    expect(lists).toBe(2)
  })
})

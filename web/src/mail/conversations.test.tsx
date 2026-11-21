import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { User } from '@/api/auth.ts'
import { bodiesTo, type FakeMailState, messageItem, messageView, queriesTo, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const ALICE: User = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true }
const UNREAD = { seen: false, flagged: false, answered: false, forwarded: false, draft: false, to_sort: false }

// "plan" is three messages, the latest unread; "lunch" is one.
const latest = messageItem(WORK, 'INBOX', 4, { subject: 'Re: plan', from: { name: 'Bob', address: 'bob@example.org' }, flags: UNREAD })
const lunch = messageItem(WORK, 'INBOX', 3, { subject: 'lunch' })
const middle = messageItem(WORK, 'INBOX', 2, { subject: 'Re: plan', from: { name: 'Ann', address: 'ann@example.org' }, has_attachments: true })
const first = messageItem(WORK, 'INBOX', 1, { subject: 'plan', from: { name: 'Ann', address: 'ann@example.org' } })
const plan = [latest.ref, middle.ref, first.ref]

function state(fields: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 1, total: 4 },
        { path: 'Archive', role: 'archive', unread: 0, total: 0 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
      ],
    },
    messages: { '1/INBOX': [latest, lunch, middle, first] },
    views: Object.fromEntries([latest, lunch, middle, first].map((item) => [item.ref, messageView(item)])),
    threads: Object.fromEntries(plan.map((ref) => [ref, 'plan'])),
    ...fields,
  }
}

const messagesList = (): Promise<HTMLElement> => screen.findByRole('list', { name: 'Messages' })

async function planRow(): Promise<HTMLElement> {
  const list = await messagesList()
  const link = await within(list).findByRole('link', { name: /Ann, Bob/ })
  const row = link.closest('li')
  if (row === null) {
    throw new Error('no row for the conversation')
  }
  return row
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('conversations in the list', () => {
  it('show a conversation as one row: its writers, its count, unread while one of it is', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: ALICE })

    const row = await planRow()
    const link = within(row).getByRole('link')
    expect(link).toHaveTextContent('Ann, Bob')
    expect(link).toHaveTextContent('3 messages in the conversation')
    expect(link).toHaveTextContent('Unread: Re: plan')
    expect(within(link).getByRole('img', { name: 'Has attachments' })).toBeInTheDocument()
    const list = await messagesList()
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
    // A conversation of one message is a message's row.
    const single = within(list).getByRole('link', { name: /lunch/ })
    expect(single).toHaveTextContent('Sender 3')
    expect(single).not.toHaveTextContent('in the conversation')
    expect(queriesTo(fetchMock, '/api/v1/mail/accounts/1/messages').map((query) => query.get('group'))).toEqual(['thread'])
  })

  it('group a search and a view as a folder', async () => {
    const fetchMock = stubMailApi(state({ searchResult: { messages: [latest, middle], next_cursor: null, failures: [] } }))
    renderMailApp('/mail?q=%7B%22from%22%3A%22ann%22%7D', { user: ALICE })

    const results = await screen.findByRole('list', { name: 'Search results' })
    expect(await within(results).findByRole('link', { name: /Ann, Bob/ })).toHaveTextContent('2 messages in the conversation')
    expect(bodiesTo(fetchMock, '/api/v1/mail/search')).toEqual([{ from: 'ann', group: 'thread' }])

    await userEvent.click(await screen.findByRole('link', { name: /^All inboxes/ }))
    const list = await messagesList()
    expect(await within(list).findByRole('link', { name: /Ann, Bob/ })).toHaveTextContent('3 messages in the conversation')
    expect(queriesTo(fetchMock, '/api/v1/mail/views/all_inboxes/messages').map((query) => query.get('group'))).toEqual(['thread'])
  })

  it('are not grouped when the user turned them off', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: { ...ALICE, conversations: false } })

    const list = await messagesList()
    await within(list).findByRole('link', { name: /lunch/ })
    expect(within(list).getAllByRole('listitem')).toHaveLength(4)
    expect(queriesTo(fetchMock, '/api/v1/mail/accounts/1/messages').map((query) => query.get('group'))).toEqual([null])
  })

  it('open in the reading pane: the thread oldest first, its latest message read, every message of it marked read', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: ALICE })

    await userEvent.click(within(await planRow()).getByRole('link'))
    const thread = await screen.findByRole('navigation', { name: 'Conversation' })
    expect(await within(thread).findByText('3 messages in this conversation')).toBeInTheDocument()
    const items = within(thread).getAllByRole('link')
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining('Ann'),
      expect.stringContaining('Ann'),
      expect.stringContaining('Bob'),
    ])
    expect(items[2]).toHaveAttribute('aria-current')
    expect(items[0]).not.toHaveAttribute('aria-current')
    expect(await screen.findByRole('heading', { name: 'Re: plan' })).toBeInTheDocument()
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toEqual([{ refs: plan, seen: true }])
    })
    await waitFor(async () => {
      expect(within(await planRow()).getByRole('link')).not.toHaveTextContent('Unread')
    })
    // The thread is read again with them.
    await waitFor(() => {
      expect(within(thread).queryByText(/Unread/)).not.toBeInTheDocument()
    })

    // Another message of it opens below the thread; the row stays the current one.
    const [opening] = items
    if (opening === undefined) {
      throw new Error('no first message')
    }
    await userEvent.click(opening)
    expect(await screen.findByRole('heading', { name: 'plan' })).toBeInTheDocument()
    expect(within(await planRow()).getByRole('link')).toHaveAttribute('aria-current')
  })

  it('mark the whole conversation read when followed by a link, whatever loads first', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp(`/mail?message=${latest.ref}&thread=${latest.ref}`, { user: ALICE })

    expect(await screen.findByRole('heading', { name: 'Re: plan' })).toBeInTheDocument()
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag').flatMap((body) => (body as { refs: string[] }).refs)).toEqual(
        expect.arrayContaining(plan),
      )
    })
  })

  it('archive, star and select every message of a conversation', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: ALICE })

    await userEvent.click(within(await planRow()).getByRole('button', { name: 'Star' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toEqual([{ refs: plan, flagged: true }])
    })
    await waitFor(async () => {
      expect(within(await planRow()).getByRole('button', { name: 'Star' })).toHaveAttribute('aria-pressed', 'true')
    })

    await userEvent.click(within(await planRow()).getByRole('checkbox'))
    const actions = await screen.findByRole('group', { name: 'Message actions' })
    expect(actions).toHaveTextContent('1 selected')
    await userEvent.click(within(actions).getByRole('button', { name: 'Archive' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/archive')).toEqual([{ refs: plan, source_folder: 'INBOX' }])
    })
    const list = await messagesList()
    await waitFor(() => {
      expect(within(list).queryByRole('link', { name: /Ann, Bob/ })).not.toBeInTheDocument()
    })
    expect(within(list).getByRole('link', { name: /lunch/ })).toBeInTheDocument()
    expect(document.getElementById('toast-status')).toHaveTextContent('Archived 3 messages.')
  })

  it('takes back a star on a conversation none of which was starred', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', { user: ALICE })

    await userEvent.click(within(await planRow()).getByRole('button', { name: 'Star' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Undo' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toEqual([
        { refs: plan, flagged: true },
        { refs: plan, flagged: false },
      ])
    })
  })

  it('ask before trashing a conversation whose newest sender is new to it, and can take the message shown alone', async () => {
    const fetchMock = stubMailApi(state({ newSenders: [latest.ref] }))
    renderMailApp('/mail', { user: ALICE })

    await userEvent.click(within(await planRow()).getByRole('checkbox'))
    await userEvent.keyboard('#')
    const dialog = await screen.findByRole('dialog', { name: 'A sender new to this conversation' })
    expect(dialog).toHaveTextContent('This conversation has 3 messages, and the newest is from a sender new to it')
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/trash')).toEqual([])

    await userEvent.keyboard('#')
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Only the message shown' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/trash')).toEqual([{ refs: [latest.ref], source_folder: 'INBOX' }])
    })
  })

  it('archive, move and report such a conversation whole once told to; a star asks nothing', async () => {
    const fetchMock = stubMailApi(state({ newSenders: [latest.ref] }))
    renderMailApp('/mail', { user: ALICE })

    await userEvent.click(within(await planRow()).getByRole('button', { name: 'Star' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toEqual([{ refs: plan, flagged: true }])
    })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    await userEvent.click(within(await planRow()).getByRole('checkbox'))
    await userEvent.keyboard('e')
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'All 3 messages' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/archive')).toEqual([{ refs: plan, source_folder: 'INBOX' }])
    })
  })
})

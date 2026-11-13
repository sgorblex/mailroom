import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { bodiesTo, messageItem, messageView, refusal, stubMailApi, type FakeMailState } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const UNREAD = { seen: false, flagged: false, answered: false, draft: false }
const inbox = Array.from({ length: 5 }, (_, index) =>
  messageItem('work', 'INBOX', 10 - index, index === 0 ? { flags: UNREAD } : {}),
)
const [unread, second, third] = inbox

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: ['work', 'home'],
    folders: {
      work: [
        { path: 'INBOX', role: 'inbox', unread: 3, total: 5 },
        { path: 'Sent', role: 'sent', unread: 0, total: 4 },
        { path: 'Drafts', role: 'drafts', unread: 0, total: 1 },
        { path: 'Archive', role: 'archive', unread: 0, total: 2 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
        { path: 'Projects', role: null, unread: 0, total: 0 },
        // Holds only folders: never a destination.
        { path: 'Lists', role: null, unread: 0, total: 0, selectable: false },
      ],
      home: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 1 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
      ],
    },
    messages: {
      'work/INBOX': inbox,
      'home/INBOX': [messageItem('home', 'INBOX', 1, { subject: 'From home' })],
    },
    views: Object.fromEntries(inbox.map((item) => [item.ref, messageView(item)])),
    ...overrides,
  }
}

const messagesList = (): Promise<HTMLElement> => screen.findByRole('list', { name: 'Messages' })
const FLAG = '/api/v1/mail/messages/flag'
const TRASH = '/api/v1/mail/messages/trash'

/** The toolbar's live region: `polite` (role status) for a result, `assertive` (role alert) for a failure. */
function report(live: 'polite' | 'assertive'): HTMLElement {
  const region = document.querySelector<HTMLElement>(`[aria-live="${live}"]`)
  if (region === null) {
    throw new Error(`no ${live} live region`)
  }
  return region
}

async function reported(live: 'polite' | 'assertive', text: string): Promise<void> {
  await waitFor(() => {
    expect(report(live)).toHaveTextContent(text)
  })
}

/** A promise the test resolves itself, for an answer the fake holds back. */
function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve = (): void => undefined
  const promise = new Promise<void>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
const actions = (): Promise<HTMLElement> => screen.findByRole('group', { name: 'Message actions' })
const rowLink = (list: HTMLElement, subject: string): HTMLElement =>
  within(list).getByRole('link', { name: new RegExp(`${subject}Preview`) })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('triage', () => {
  it('selects rows one by one and all at once', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    expect(screen.queryByRole('group', { name: 'Message actions' })).not.toBeInTheDocument()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
    expect(await actions()).toHaveTextContent('2 selected')

    const all = screen.getByRole('checkbox', { name: 'Select all' })
    expect(all).toHaveProperty('indeterminate', true)
    await userEvent.click(all)
    expect(await actions()).toHaveTextContent('5 selected')
    await userEvent.click(all)
    expect(within(list).queryAllByRole('checkbox', { checked: true })).toHaveLength(0)
    expect(screen.queryByRole('group', { name: 'Message actions' })).not.toBeInTheDocument()
  })

  it('archives the selection: its rows go and the counts follow, without a reload', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 10' }))
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Archive' }))

    await reported('polite', 'Archived 2 messages.')
    expect(within(list).queryByRole('link', { name: /Message 10Preview/ })).not.toBeInTheDocument()
    expect(within(list).queryByRole('link', { name: /Message 9Preview/ })).not.toBeInTheDocument()
    expect(within(list).getAllByRole('listitem')).toHaveLength(3)
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/archive')).toEqual([{ refs: [unread?.ref, second?.ref] }])
    const folders = screen.getByRole('tree', { name: 'Folders of work' })
    expect(within(folders).getByRole('treeitem', { name: 'Inbox, 2 unread' })).toBeInTheDocument()
    expect(within(folders).getByRole('treeitem', { name: 'Archive, 1 unread' })).toBeInTheDocument()
    // Only the first load of the list and of the folders: nothing was read again.
    expect(fetchMock.mock.calls.filter(([path]) => String(path).includes('/folders'))).toHaveLength(1)
  })

  it('trashes the open message when nothing is selected, and closes it', async () => {
    const fetchMock = stubMailApi(state())
    const history = renderMailApp(`/mail?message=${String(second?.ref)}`)

    await screen.findByRole('region', { name: 'Message body' })
    expect(await actions()).toHaveTextContent('This message')
    await userEvent.click(screen.getByRole('button', { name: 'Move to Trash' }))
    await reported('polite', 'Moved 1 message to Trash.')
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/trash')).toEqual([{ refs: [second?.ref] }])
    await waitFor(() => {
      expect(history.location.search).not.toContain('message=')
    })
    expect(within(await messagesList()).queryByRole('link', { name: /Message 9Preview/ })).not.toBeInTheDocument()
  })

  it('offers no Archive when the archive folder only holds folders', async () => {
    const folders = {
      ...state().folders,
      work: (state().folders['work'] ?? []).map((folder) =>
        folder.role === 'archive' ? { ...folder, selectable: false } : folder,
      ),
    }
    stubMailApi(state({ folders }))
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
    expect(within(await actions()).getByRole('button', { name: 'Move to Trash' })).toBeInTheDocument()
    expect(within(await actions()).queryByRole('button', { name: 'Archive' })).not.toBeInTheDocument()
  })

  it('moves to a folder from the picker, without Drafts, Sent, the folder they are in or a level holding only folders', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
    const move = within(await actions()).getByRole('button', { name: 'Move to…' })
    await userEvent.click(move)
    expect(move).toHaveAttribute('aria-expanded', 'true')
    const picker = screen.getByRole('dialog', { name: 'Move to' })
    expect(within(picker).getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Archive',
      'Trash',
      'Projects',
    ])
    expect(within(picker).getByRole('button', { name: 'Archive' })).toHaveFocus()
    await userEvent.click(within(picker).getByRole('button', { name: 'Projects' }))

    await reported('polite', 'Moved 1 message to Projects.')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/move')).toEqual([{ refs: [third?.ref], folder: 'Projects' }])
    expect(within(list).queryByRole('link', { name: /Message 8Preview/ })).not.toBeInTheDocument()
  })

  it('shows a failed group in the mail wording and updates the others', async () => {
    const results = {
      messages: [messageItem('home', 'INBOX', 1, { subject: 'From home' }), messageItem('work', 'INBOX', 9)],
      next_cursor: null,
      failures: [],
    }
    stubMailApi(
      state({
        searchResult: results,
        messages: { 'work/INBOX': inbox, 'home/INBOX': [results.messages[0] ?? messageItem('home', 'INBOX', 1)] },
        groupFailures: { 'work/INBOX': 'stale_reference' },
      }),
    )
    renderMailApp('/mail?q=%7B%7D')

    const list = await screen.findByRole('list', { name: 'Search results' })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all' }))
    // Not every account of the selection has an Archive folder.
    expect(within(await actions()).queryByRole('button', { name: 'Archive' })).not.toBeInTheDocument()
    // Two accounts: no one folder list to move to.
    expect(within(await actions()).queryByRole('button', { name: 'Move to…' })).not.toBeInTheDocument()
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Move to Trash' }))

    await reported('assertive', 'Moved 1 message to Trash.')
    const alert = report('assertive')
    expect(alert).toHaveAttribute('role', 'alert')
    expect(alert).toHaveTextContent('Some messages could not be changed:')
    // The folder as the sidebar names it.
    expect(alert).toHaveTextContent('work, Inbox: This message is out of date')
    expect(alert).not.toHaveTextContent('Server text')
    expect(report('polite')).toBeEmptyDOMElement()
    expect(within(list).queryByRole('link', { name: /From home/ })).not.toBeInTheDocument()
    expect(rowLink(list, 'Message 9')).toBeInTheDocument()
    // The failed row stays selected, for another try.
    expect(await actions()).toHaveTextContent('1 selected')
  })

  it('words a batch where every group failed as the refusal', async () => {
    stubMailApi(state({ groupFailures: { 'work/INBOX': 'not_found' } }))
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Archive' }))
    await reported('assertive', 'Not every message is still in that folder. Reload the list.')
    expect(rowLink(list, 'Message 9')).toBeInTheDocument()
    // The notice's text is hidden from screen readers; the region holding it describes the button.
    expect(screen.getByRole('button', { name: 'Dismiss' })).toHaveAccessibleDescription(
      'Not every message is still in that folder. Reload the list.',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(report('assertive')).toBeEmptyDOMElement()
    expect(screen.queryByRole('button', { name: 'Dismiss' })).not.toBeInTheDocument()
  })

  it('stars, unstars and marks read and unread in place', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    const group = await actions()
    await userEvent.click(within(group).getByRole('button', { name: 'Star' }))
    await reported('polite', 'Starred 1 message.')
    expect(within(rowLink(list, 'Message 9')).getByLabelText('Starred')).toBeInTheDocument()
    await userEvent.click(within(group).getByRole('button', { name: 'Unstar' }))
    await waitFor(() => {
      expect(within(rowLink(list, 'Message 9')).queryByLabelText('Starred')).not.toBeInTheDocument()
    })

    await userEvent.click(within(group).getByRole('button', { name: 'Mark as unread' }))
    expect(await within(list).findByRole('link', { name: /Unread:\s*Message 9Preview/ })).toBeInTheDocument()
    const folders = screen.getByRole('tree', { name: 'Folders of work' })
    expect(within(folders).getByRole('treeitem', { name: 'Inbox, 4 unread' })).toBeInTheDocument()
    await userEvent.click(within(group).getByRole('button', { name: 'Mark as read' }))
    expect(await within(folders).findByRole('treeitem', { name: 'Inbox, 3 unread' })).toBeInTheDocument()
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toEqual([
      { refs: [second?.ref], flagged: true },
      { refs: [second?.ref], flagged: false },
      { refs: [second?.ref], seen: false },
      { refs: [second?.ref], seen: true },
    ])
  })

  it('announces the same outcome again: the region empties while the next action runs', async () => {
    const secondArchive = deferred()
    let archives = 0
    stubMailApi(
      state({
        hold: (request) => {
          if (request === 'POST /api/v1/mail/messages/archive') {
            archives += 1
            return archives === 2 ? secondArchive.promise : undefined
          }
          return undefined
        },
      }),
    )
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Archive' }))
    await reported('polite', 'Archived 1 message.')
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Archive' }))
    await waitFor(() => {
      expect(report('polite')).toBeEmptyDOMElement()
    })
    secondArchive.resolve()
    await reported('polite', 'Archived 1 message.')
    expect(within(list).queryByRole('link', { name: /Message 8Preview/ })).not.toBeInTheDocument()
  })

  it('lists a folder that failed in several batches once', async () => {
    const many = (account: string, count: number): ReturnType<typeof messageItem>[] =>
      Array.from({ length: count }, (_, index) => messageItem(account, 'INBOX', 1000 - index))
    const work = many('work', 60)
    const home = many('home', 50)
    stubMailApi(
      state({
        messages: { 'work/INBOX': work, 'home/INBOX': home },
        searchResult: { messages: [...work, ...home], next_cursor: null, failures: [] },
        // The first batch is all work: refused whole; the second fails work's group in a 200.
        groupFailures: { 'work/INBOX': 'stale_reference' },
      }),
    )
    renderMailApp('/mail?q=%7B%7D')

    await screen.findByRole('list', { name: 'Search results' })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all' }))
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Move to Trash' }))

    await reported('assertive', 'Moved 50 messages to Trash.')
    expect(within(report('assertive')).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      expect.stringContaining('work, Inbox: This message is out of date'),
    ])
  })

  it('sends a large selection 50 at a time, and reports a refused batch without stopping', async () => {
    const many = (account: string, folder: string, count: number): ReturnType<typeof messageItem>[] =>
      Array.from({ length: count }, (_, index) => messageItem(account, folder, 1000 - index))
    const work = many('work', 'INBOX', 50)
    const projects = many('work', 'Projects', 50)
    const home = many('home', 'INBOX', 10)
    const fetchMock = stubMailApi(
      state({
        messages: { 'work/INBOX': work, 'work/Projects': projects, 'home/INBOX': home },
        searchResult: { messages: [...work, ...projects, ...home], next_cursor: null, failures: [] },
        // The second batch is all Projects: refused as a whole.
        groupFailures: { 'work/Projects': 'stale_reference' },
      }),
    )
    renderMailApp('/mail?q=%7B%7D')

    await screen.findByRole('list', { name: 'Search results' })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all' }))
    expect(await actions()).toHaveTextContent('110 selected')
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Move to Trash' }))

    await reported('assertive', 'Moved 60 messages to Trash.')
    expect(report('assertive')).toHaveTextContent('work, Projects: This message is out of date')
    expect((bodiesTo(fetchMock, TRASH) as { refs: string[] }[]).map((body) => body.refs)).toEqual([
      work.map((item) => item.ref),
      projects.map((item) => item.ref),
      home.map((item) => item.ref),
    ])
    expect(await actions()).toHaveTextContent('50 selected')
  })

  it('starts a new folder from what it reads: no selection, no flags changed before', async () => {
    const fake = state({ messages: { 'work/INBOX': inbox, 'work/Projects': [] } })
    stubMailApi(fake)
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Mark as unread' }))
    expect(await within(list).findByRole('link', { name: /Unread:\s*Message 9Preview/ })).toBeInTheDocument()
    // Another client reads it meanwhile.
    fake.messages['work/INBOX'] = (fake.messages['work/INBOX'] ?? []).map((item) =>
      item.ref === second?.ref ? { ...item, flags: { ...item.flags, seen: true } } : item,
    )

    const folders = screen.getByRole('tree', { name: 'Folders of work' })
    await userEvent.click(within(folders).getByRole('treeitem', { name: 'Projects' }))
    expect(await screen.findByText('No messages in this folder.')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Message actions' })).not.toBeInTheDocument()
    await userEvent.click(within(folders).getByRole('treeitem', { name: /^Inbox/ }))

    const again = await messagesList()
    expect(within(again).getByRole('checkbox', { name: 'Select Message 9' })).not.toBeChecked()
    expect(rowLink(again, 'Message 9')).not.toHaveAccessibleName(/Unread:/)
  })

  it('keeps Load more, without calling the folder empty, once every loaded row moved away', async () => {
    const long = Array.from({ length: 26 }, (_, index) => messageItem('work', 'INBOX', 100 - index))
    stubMailApi(state({ messages: { 'work/INBOX': long } }))
    renderMailApp('/mail')

    await messagesList()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all' }))
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Archive' }))
    await reported('polite', 'Archived 25 messages.')
    expect(screen.queryByText('No messages in this folder.')).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Select all' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
    expect(rowLink(await messagesList(), 'Message 75')).toBeInTheDocument()
  })

  it('goes on below the last loaded row once it moved away, showing no row twice', async () => {
    const long = Array.from({ length: 26 }, (_, index) => messageItem('work', 'INBOX', 100 - index))
    stubMailApi(state({ messages: { 'work/INBOX': long } }))
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 76' }))
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Archive' }))
    await reported('polite', 'Archived 1 message.')

    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
    expect(await within(list).findByRole('link', { name: /Message 75Preview/ })).toBeInTheDocument()
    const subjects = within(list)
      .getAllByRole('link')
      .map((link) => /Message \d+/.exec(link.textContent)?.[0])
    expect(subjects).toEqual(long.filter((item) => item.subject !== 'Message 76').map((item) => item.subject))
  })

  it('hides Archive for an account without an Archive folder', async () => {
    stubMailApi(state())
    renderMailApp('/mail?account=home')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select From home' }))
    const group = await actions()
    expect(within(group).getByRole('button', { name: 'Move to Trash' })).toBeInTheDocument()
    expect(within(group).queryByRole('button', { name: 'Archive' })).not.toBeInTheDocument()
  })
})

describe('marking read on open', () => {
  it('marks an unread message read once its view loads, and updates the row and count', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    const folders = await screen.findByRole('tree', { name: 'Folders of work' })
    expect(within(folders).getByRole('treeitem', { name: 'Inbox, 3 unread' })).toBeInTheDocument()
    await userEvent.click(within(list).getByRole('link', { name: /Unread:\s*Message 10Preview/ }))
    expect(await within(folders).findByRole('treeitem', { name: 'Inbox, 2 unread' })).toBeInTheDocument()
    expect(within(list).queryByRole('link', { name: /Unread:/ })).not.toBeInTheDocument()
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toEqual([{ refs: [unread?.ref], seen: true }])
    // Nothing reported: it is not an action of the user's.
    expect(report('polite')).toBeEmptyDOMElement()

    // Marked unread again while open, it stays so.
    await userEvent.click(screen.getByRole('button', { name: 'Mark as unread' }))
    expect(await within(list).findByRole('link', { name: /Unread:\s*Message 10Preview/ })).toBeInTheDocument()
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toHaveLength(2)
  })

  it('leaves a read message alone', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp(`/mail?message=${String(second?.ref)}`)

    await screen.findByRole('region', { name: 'Message body' })
    await actions()
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toEqual([])
  })

  it('leaves it unread, without a word, when marking fails', async () => {
    stubMailApi(state({ refuse: { 'POST /api/v1/mail/messages/flag': refusal(429, 'busy') } }))
    renderMailApp(`/mail?message=${String(unread?.ref)}`)

    await screen.findByRole('region', { name: 'Message body' })
    const list = await messagesList()
    expect(await screen.findByRole('button', { name: 'Mark as read' })).toBeInTheDocument()
    expect(within(list).getByRole('link', { name: /Unread:\s*Message 10Preview/ })).toBeInTheDocument()
    expect(screen.getAllByRole('alert')).toEqual([report('assertive')])
    expect(report('assertive')).toBeEmptyDOMElement()
  })

  it('sends the next action on that message only once the mark-read has answered', async () => {
    const markRead = deferred()
    let held = false
    const fetchMock = stubMailApi(
      state({
        hold: (request) => {
          if (request === `POST ${FLAG}` && !held) {
            held = true
            return markRead.promise
          }
          return undefined
        },
      }),
    )
    renderMailApp(`/mail?message=${String(unread?.ref)}`)
    await screen.findByRole('region', { name: 'Message body' })
    const folders = await screen.findByRole('tree', { name: 'Folders of work' })
    await waitFor(() => {
      expect(bodiesTo(fetchMock, FLAG)).toHaveLength(1)
    })

    await userEvent.keyboard('u')
    // The reader's choice waits for the mark-read still in flight.
    expect(bodiesTo(fetchMock, FLAG)).toEqual([{ refs: [unread?.ref], seen: true }])
    markRead.resolve()
    await waitFor(() => {
      expect(bodiesTo(fetchMock, FLAG)).toEqual([
        { refs: [unread?.ref], seen: true },
        { refs: [unread?.ref], seen: false },
      ])
    })
    // Applied in that order too: unread, as chosen, and the count as it started.
    expect(await within(await messagesList()).findByRole('link', { name: /Unread:\s*Message 10Preview/ })).toBeInTheDocument()
    await waitFor(() => {
      expect(within(folders).getByRole('treeitem', { name: 'Inbox, 3 unread' })).toBeInTheDocument()
    })
  })
})

describe('keyboard shortcuts', () => {
  it('moves through the list with j and k', async () => {
    const history = renderAt('/mail')
    await messagesList()

    await userEvent.keyboard('j')
    await waitFor(() => {
      expect(history.location.search).toContain(`message=${String(unread?.ref)}`)
    })
    await userEvent.keyboard('j')
    await waitFor(() => {
      expect(history.location.search).toContain(`message=${String(second?.ref)}`)
    })
    await userEvent.keyboard('k')
    await waitFor(() => {
      expect(history.location.search).toContain(`message=${String(unread?.ref)}`)
    })
  })

  it('selects with x and clears with Escape', async () => {
    renderAt(`/mail?message=${String(second?.ref)}`)
    const list = await messagesList()
    await actions()

    await userEvent.keyboard('x')
    expect(within(list).getByRole('checkbox', { name: 'Select Message 9' })).toBeChecked()
    expect(await actions()).toHaveTextContent('1 selected')
    await userEvent.keyboard('{Escape}')
    expect(within(list).getByRole('checkbox', { name: 'Select Message 9' })).not.toBeChecked()
  })

  it.each([
    ['e', '/api/v1/mail/messages/archive', { refs: [second?.ref] }],
    ['#', '/api/v1/mail/messages/trash', { refs: [second?.ref] }],
    ['s', '/api/v1/mail/messages/flag', { refs: [second?.ref], flagged: true }],
    ['u', '/api/v1/mail/messages/flag', { refs: [second?.ref], seen: false }],
  ])('%s acts on the open message', async (key, path, body) => {
    const fetchMock = stubMailApi(state())
    renderMailApp(`/mail?message=${String(second?.ref)}`)
    await actions()

    await userEvent.keyboard(key)
    await waitFor(() => {
      expect(bodiesTo(fetchMock, path)).toEqual([body])
    })
  })

  it('opens the move picker with v and closes it with Escape before clearing the selection', async () => {
    renderAt('/mail')
    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    await actions()

    await userEvent.keyboard('v')
    expect(await screen.findByRole('dialog', { name: 'Move to' })).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Move to…' })).toHaveFocus()
    expect(within(list).getByRole('checkbox', { name: 'Select Message 9' })).toBeChecked()
    await userEvent.keyboard('{Escape}')
    expect(within(list).getByRole('checkbox', { name: 'Select Message 9' })).not.toBeChecked()
  })

  it('moves on with each j while the next message is still loading', async () => {
    const views = deferred()
    stubMailApi(state({ hold: (request) => (request.startsWith('GET /api/v1/mail/messages/') ? views.promise : undefined) }))
    const history = renderMailApp('/mail')
    await messagesList()

    await userEvent.keyboard('jjj')
    await waitFor(() => {
      expect(history.location.search).toContain(`message=${String(third?.ref)}`)
    })
    views.resolve()
  })

  it.each([
    ['AltGr (Ctrl and Alt, as on Windows)', { ctrlKey: true, altKey: true }, true],
    ['Option (Alt, as on a Mac)', { altKey: true }, false],
  ])('trashes with a # typed with %s, as on an Italian keyboard', async (_name, modifiers, altGraph) => {
    const fetchMock = stubMailApi(state())
    renderMailApp(`/mail?message=${String(second?.ref)}`)
    await actions()

    const event = new KeyboardEvent('keydown', { key: '#', bubbles: true, cancelable: true, ...modifiers })
    Object.defineProperty(event, 'getModifierState', { value: (key: string) => altGraph && key === 'AltGraph' })
    fireEvent(document.body, event)
    await waitFor(() => {
      expect(bodiesTo(fetchMock, TRASH)).toEqual([{ refs: [second?.ref] }])
    })
  })

  it('works right after ticking a checkbox', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')
    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))

    await userEvent.keyboard('e')
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/archive')).toEqual([{ refs: [second?.ref] }])
    })
  })

  it('is ignored while typing, with Ctrl or Meta, with Alt on a letter and on a held key', async () => {
    const fetchMock = stubMailApi(state())
    const history = renderMailApp(`/mail?message=${String(second?.ref)}`)
    await actions()

    await userEvent.type(screen.getByRole('searchbox', { name: 'Words' }), 'jkxes#uv')
    await userEvent.click(screen.getByRole('button', { name: 'More options' }))
    await userEvent.selectOptions(screen.getByLabelText('Account'), 'All accounts')
    screen.getByLabelText('Account').focus()
    await userEvent.keyboard('e')
    ;(document.activeElement as HTMLElement | null)?.blur()
    await userEvent.keyboard('{Control>}e{/Control}{Meta>}#{/Meta}{Alt>}s{/Alt}{Control>}j{/Control}')
    // A key held down repeats: only j and k move on with it.
    fireEvent.keyDown(document.body, { key: 'e', repeat: true })
    fireEvent.keyDown(document.body, { key: '#', repeat: true })
    // An action reaches fetch only after an await: let any have started before looking.
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(history.location.search).toContain(`message=${String(second?.ref)}`)
    const writes = (): unknown[] =>
      fetchMock.mock.calls.filter(([path]) => /\/messages\/(move|archive|trash|flag)$/.test(String(path)))
    expect(writes()).toEqual([])
    // The same key pressed, not held, does act: the check above would have seen it.
    fireEvent.keyDown(document.body, { key: 'e' })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/archive')).toEqual([{ refs: [second?.ref] }])
  })
})

function renderAt(path: string): ReturnType<typeof renderMailApp> {
  stubMailApi(state())
  return renderMailApp(path)
}

import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DEFAULT_APPEARANCE, type UserJson } from '@/api/auth.ts'
import { FakeEventSource, stubEventSource } from '@/test/fake-event-source.ts'
import { bodiesTo, type FakeMailState, HOME, messageItem, messageView, refusal, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { scrollListToEnd } from '@/test/fake-virtualizer.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const UNREAD = { seen: false, flagged: false, answered: false, forwarded: false, draft: false, to_sort: false }
const inbox = Array.from({ length: 5 }, (_, index) =>
  messageItem(WORK, 'INBOX', 10 - index, index === 0 ? { flags: UNREAD } : {}),
)
const [unread, second, third] = inbox

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 3, total: 5 },
        { path: 'Sent', role: 'sent', unread: 0, total: 4 },
        { path: 'Drafts', role: 'drafts', unread: 0, total: 1 },
        { path: 'Archive', role: 'archive', unread: 0, total: 2 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
        { path: 'Projects', role: null, unread: 0, total: 0 },
        // Holds only folders: never a destination.
        { path: 'Lists', role: null, unread: 0, total: 0, selectable: false },
      ],
      [HOME]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 1 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
      ],
    },
    messages: {
      '1/INBOX': inbox,
      '2/INBOX': [messageItem(HOME, 'INBOX', 1, { subject: 'From home' })],
    },
    views: Object.fromEntries(inbox.map((item) => [item.ref, messageView(item)])),
    ...overrides,
  }
}

// The first wait after a render: on a busy CI runner the cold mount of the mail screen alone can take a second.
const messagesList = (): Promise<HTMLElement> => screen.findByRole('list', { name: 'Messages' }, { timeout: 3_000 })
const FLAG = '/api/v1/mail/messages/flag'
const TRASH = '/api/v1/mail/messages/trash'

/** Where an outcome is announced: the toast's `polite` region for a result, the toolbar's `assertive` one for a failure. */
function report(live: 'polite' | 'assertive'): HTMLElement {
  const region = document.getElementById(live === 'polite' ? 'toast-status' : 'triage-outcome-alert')
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

/** The live updates stream, opened. */
async function openStream(): Promise<FakeEventSource> {
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

/** A live `folder_changed` event: the folder's counts as the server has them now. */
function sendCounts(source: FakeEventSource, folder: string, unread: number, total: number): void {
  act(() => {
    source.send('folder_changed', { account: WORK, folder, unread, total })
  })
}
const actions = (): Promise<HTMLElement> => screen.findByRole('group', { name: 'Message actions' })
const selection = (): Promise<HTMLElement> => screen.findByRole('group', { name: 'Selected messages' })
const rowLink = (list: HTMLElement, subject: string): HTMLElement =>
  within(list).getByRole('link', { name: new RegExp(`${subject}Preview`) })
/** The row of the message with `subject`, its checkbox and star toggle included. */
function row(list: HTMLElement, subject: string): HTMLElement {
  const item = rowLink(list, subject).closest('li')
  if (item === null) {
    throw new Error(`no row for ${subject}`)
  }
  return item
}
const starToggle = (list: HTMLElement, subject: string): HTMLElement =>
  within(row(list, subject)).getByRole('button', { name: 'Star' })
const toSortToggle = (list: HTMLElement, subject: string): HTMLElement =>
  within(row(list, subject)).getByRole('button', { name: 'To sort' })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('triage', () => {
  it('selects rows one by one and all at once', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    expect(screen.queryByRole('group', { name: 'Selected messages' })).not.toBeInTheDocument()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
    expect(await selection()).toHaveTextContent('2 selected')

    const all = screen.getByRole('checkbox', { name: 'Select all' })
    expect(all).toHaveProperty('indeterminate', true)
    await userEvent.click(all)
    expect(await selection()).toHaveTextContent('5 selected')
    await userEvent.click(all)
    expect(within(list).queryAllByRole('checkbox', { checked: true })).toHaveLength(0)
    expect(screen.queryByRole('group', { name: 'Selected messages' })).not.toBeInTheDocument()
  })

  it('selects every row between two checkboxes with Shift+click, and unselects them so', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    const user = userEvent.setup()
    await user.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    await user.keyboard('{Shift>}')
    await user.click(within(list).getByRole('checkbox', { name: 'Select Message 7' }))
    await user.keyboard('{/Shift}')
    expect(await selection()).toHaveTextContent('3 selected')
    expect(within(list).getByRole('checkbox', { name: 'Select Message 8' })).toBeChecked()

    await user.keyboard('{Shift>}')
    await user.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
    await user.keyboard('{/Shift}')
    expect(await selection()).toHaveTextContent('1 selected')
    expect(within(list).getByRole('checkbox', { name: 'Select Message 9' })).toBeChecked()
  })

  it('archives the selection: its rows go and the counts follow, without a reload', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 10' }))
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Archive' }))

    await reported('polite', 'Archived 2 messages.')
    expect(within(list).queryByRole('link', { name: /Message 10Preview/ })).not.toBeInTheDocument()
    expect(within(list).queryByRole('link', { name: /Message 9Preview/ })).not.toBeInTheDocument()
    expect(within(list).getAllByRole('listitem')).toHaveLength(3)
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/archive')).toEqual([
      { refs: [unread?.ref, second?.ref], source_folder: 'INBOX' },
    ])
    const folders = screen.getByRole('tree', { name: 'Folders of work' })
    expect(within(folders).getByRole('treeitem', { name: 'Inbox, 2 unread' })).toBeInTheDocument()
    expect(within(folders).getByRole('treeitem', { name: 'Archive, 1 unread' })).toBeInTheDocument()
    // Only the first load of the list and of the folders: nothing was read again.
    expect(fetchMock.mock.calls.filter(([path]) => String(path).includes('/folders'))).toHaveLength(1)
  })

  it.each([
    ['folder=Archive', { source_folder: 'Archive' }],
    [`q=${encodeURIComponent(JSON.stringify({ folder: 'Projects', subject: 'x' }))}`, { source_folder: 'Projects' }],
    [`q=${encodeURIComponent(JSON.stringify({ subject: 'x' }))}`, { source_folder: 'INBOX' }],
    [`q=${encodeURIComponent(JSON.stringify({ folder: '*', subject: 'x' }))}`, {}],
  ])('names the folder shown with %s, none over every folder', async (shown, named) => {
    const fetchMock = stubMailApi(state())
    renderMailApp(`/mail?${shown}&message=${String(second?.ref)}`)

    await screen.findByRole('region', { name: 'Message body' })
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Move to Trash' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/trash')).toEqual([{ refs: [second?.ref], ...named }])
    })
  })

  it('trashes the open message when nothing is selected, and closes it', async () => {
    const fetchMock = stubMailApi(state())
    const history = renderMailApp(`/mail?message=${String(second?.ref)}`)

    await screen.findByRole('region', { name: 'Message body' })
    // The list's toolbar is for a selection; the open message has its own bar.
    expect(screen.queryByRole('group', { name: 'Selected messages' })).not.toBeInTheDocument()
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Move to Trash' }))
    await reported('polite', 'Moved 1 message to Trash.')
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/trash')).toEqual([{ refs: [second?.ref], source_folder: 'INBOX' }])
    await waitFor(() => {
      expect(history.location.search).not.toContain('message=')
    })
    expect(within(await messagesList()).queryByRole('link', { name: /Message 9Preview/ })).not.toBeInTheDocument()
  })

  it('offers no Archive when the archive folder only holds folders', async () => {
    const folders = {
      ...state().folders,
      [WORK]: (state().folders[WORK] ?? []).map((folder) =>
        folder.role === 'archive' ? { ...folder, selectable: false } : folder,
      ),
    }
    stubMailApi(state({ folders }))
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
    expect(within(await selection()).getByRole('button', { name: 'Move to Trash' })).toBeInTheDocument()
    expect(within(await selection()).queryByRole('button', { name: 'Archive' })).not.toBeInTheDocument()
  })

  it('moves to a folder from the picker, without Drafts, Sent, the folder they are in or a level holding only folders', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
    const move = within(await selection()).getByRole('button', { name: 'Move to…' })
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
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/move')).toEqual([
      { refs: [third?.ref], folder: 'Projects', source_folder: 'INBOX' },
    ])
    expect(within(list).queryByRole('link', { name: /Message 8Preview/ })).not.toBeInTheDocument()
  })

  it('shows a failed group in the mail wording and updates the others', async () => {
    const results = {
      messages: [messageItem(HOME, 'INBOX', 1, { subject: 'From home' }), messageItem(WORK, 'INBOX', 9)],
      next_cursor: null,
      failures: [],
    }
    stubMailApi(
      state({
        searchResult: results,
        messages: { '1/INBOX': inbox, '2/INBOX': [results.messages[0] ?? messageItem(HOME, 'INBOX', 1)] },
        groupFailures: { '1/INBOX': 'stale_reference' },
      }),
    )
    renderMailApp('/mail?q=%7B%7D')

    const list = await screen.findByRole('list', { name: 'Search results' })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all' }))
    // Not every account of the selection has an Archive folder.
    expect(within(await selection()).queryByRole('button', { name: 'Archive' })).not.toBeInTheDocument()
    // Two accounts: no one folder list to move to.
    expect(within(await selection()).queryByRole('button', { name: 'Move to…' })).not.toBeInTheDocument()
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Move to Trash' }))

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
    expect(await selection()).toHaveTextContent('1 selected')
  })

  it('words a batch where every group failed as the refusal', async () => {
    stubMailApi(state({ groupFailures: { '1/INBOX': 'not_found' } }))
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Archive' }))
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
    const group = await selection()
    await userEvent.click(within(group).getByRole('button', { name: 'Star' }))
    await reported('polite', 'Starred 1 message.')
    expect(starToggle(list, 'Message 9')).toHaveAttribute('aria-pressed', 'true')
    expect(rowLink(list, 'Message 9')).toHaveAccessibleName(/Starred/)
    await userEvent.click(within(group).getByRole('button', { name: 'Unstar' }))
    await waitFor(() => {
      expect(starToggle(list, 'Message 9')).toHaveAttribute('aria-pressed', 'false')
    })
    expect(rowLink(list, 'Message 9')).not.toHaveAccessibleName(/Starred/)

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

  it('marks To sort and sorted from the toolbar and from the row, in place', async () => {
    const fetchMock = stubMailApi(state({ keywords: { [WORK]: true } }))
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    const group = await selection()
    await userEvent.click(within(group).getByRole('button', { name: 'Mark To sort' }))
    await reported('polite', 'Marked 1 message To sort.')
    expect(toSortToggle(list, 'Message 9')).toHaveAttribute('aria-pressed', 'true')
    expect(toSortToggle(list, 'Message 9')).toHaveAttribute('title', 'Mark sorted')
    expect(rowLink(list, 'Message 9')).toHaveAccessibleName(/To sort/)
    await userEvent.click(within(group).getByRole('button', { name: 'Mark sorted' }))
    await reported('polite', 'Marked 1 message sorted.')
    expect(toSortToggle(list, 'Message 9')).toHaveAttribute('aria-pressed', 'false')
    expect(rowLink(list, 'Message 9')).not.toHaveAccessibleName(/To sort/)

    await userEvent.click(toSortToggle(list, 'Message 8'))
    await reported('polite', 'Marked 1 message To sort.')
    expect(toSortToggle(list, 'Message 8')).toHaveAttribute('aria-pressed', 'true')
    expect(starToggle(list, 'Message 8')).toHaveAttribute('aria-pressed', 'false')
    expect(bodiesTo(fetchMock, FLAG)).toEqual([
      { refs: [second?.ref], to_sort: true },
      { refs: [second?.ref], to_sort: false },
      { refs: [third?.ref], to_sort: true },
    ])
  })

  it("greys To sort out, saying why, where the account's server keeps no keywords", async () => {
    const fetchMock = stubMailApi(state({ keywords: { [WORK]: false } }))
    renderMailApp(`/mail?message=${String(second?.ref)}`)

    const list = await messagesList()
    const button = within(await actions()).getByRole('button', { name: 'Mark To sort' })
    expect(button).toHaveAttribute('aria-disabled', 'true')
    expect(button).toHaveAccessibleDescription('Mark To sort (t): the mail server cannot keep the To sort mark')
    await userEvent.click(button)
    await userEvent.keyboard('t')
    const toggle = toSortToggle(list, 'Message 9')
    expect(toggle).toHaveAttribute('aria-disabled', 'true')
    expect(toggle).toHaveAccessibleDescription('Mark To sort: the mail server cannot keep the To sort mark')
    await userEvent.click(toggle)
    // The star is still there to mark with.
    await userEvent.keyboard('s')
    await reported('polite', 'Starred 1 message.')
    expect(bodiesTo(fetchMock, FLAG)).toEqual([{ refs: [second?.ref], flagged: true }])
  })

  it("offers To sort where keywords are not known, and reports the server's refusal", async () => {
    stubMailApi(state({ groupFailures: { '1/INBOX': 'unsupported' } }))
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Mark To sort' }))
    await reported('assertive', 'The mail server does not support this.')
    expect(toSortToggle(list, 'Message 9')).toHaveAttribute('aria-pressed', 'false')

    await userEvent.click(toSortToggle(list, 'Message 8'))
    await reported('assertive', 'The mail server does not support this.')
    expect(toSortToggle(list, 'Message 8')).toHaveAttribute('aria-pressed', 'false')
  })

  it("stars and unstars one row from its star, leaving the selection and the open message as they are", async () => {
    const fetchMock = stubMailApi(state())
    const history = renderMailApp(`/mail?message=${String(third?.ref)}`)

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 10' }))
    const star = starToggle(list, 'Message 9')
    expect(star).toHaveAccessibleName('Star')
    expect(star).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(star)

    await reported('polite', 'Starred 1 message.')
    expect(starToggle(list, 'Message 9')).toHaveAccessibleName('Star')
    expect(starToggle(list, 'Message 9')).toHaveAttribute('title', 'Unstar')
    expect(starToggle(list, 'Message 9')).toHaveAttribute('aria-pressed', 'true')
    expect(history.location.search).toContain(`message=${String(third?.ref)}`)
    expect(await selection()).toHaveTextContent('1 selected')
    expect(within(list).getByRole('checkbox', { name: 'Select Message 9' })).not.toBeChecked()

    await userEvent.click(starToggle(list, 'Message 9'))
    await reported('polite', 'Unstarred 1 message.')
    expect(starToggle(list, 'Message 9')).toHaveAttribute('aria-pressed', 'false')
    expect(bodiesTo(fetchMock, FLAG)).toEqual([
      { refs: [second?.ref], flagged: true },
      { refs: [second?.ref], flagged: false },
    ])
    expect(history.location.search).toContain(`message=${String(third?.ref)}`)
  })

  it("reports a row's star that failed in the toolbar's notice, leaving the row as it was", async () => {
    stubMailApi(state({ groupFailures: { '1/INBOX': 'not_found' } }))
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(starToggle(list, 'Message 9'))
    await reported('assertive', 'Not every message is still in that folder. Reload the list.')
    expect(starToggle(list, 'Message 9')).toHaveAttribute('aria-pressed', 'false')
  })

  it("leaves the s shortcut on the selection after a row's star was clicked", async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
    await userEvent.click(starToggle(list, 'Message 9'))
    await reported('polite', 'Starred 1 message.')
    // Focus is on the row's star: a button, so s is still a shortcut, and it acts on the selection.
    expect(document.activeElement).toBe(starToggle(list, 'Message 9'))
    await userEvent.keyboard('s')
    await waitFor(() => {
      expect(starToggle(list, 'Message 8')).toHaveAttribute('aria-pressed', 'true')
    })
    expect(bodiesTo(fetchMock, FLAG)).toEqual([
      { refs: [second?.ref], flagged: true },
      { refs: [third?.ref], flagged: true },
    ])
  })

  it("leaves a row's star alone while another action runs", async () => {
    const archive = deferred()
    const fetchMock = stubMailApi(
      state({
        hold: (request) => (request === 'POST /api/v1/mail/messages/archive' ? archive.promise : undefined),
      }),
    )
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Archive' }))
    const star = starToggle(list, 'Message 9')
    await waitFor(() => {
      expect(star).toHaveAttribute('aria-disabled', 'true')
    })
    await userEvent.click(star)
    expect(starToggle(list, 'Message 9')).toHaveAttribute('aria-pressed', 'false')
    expect(bodiesTo(fetchMock, FLAG)).toEqual([])

    archive.resolve()
    await reported('polite', 'Archived 1 message.')
    expect(starToggle(list, 'Message 9')).toHaveAttribute('aria-disabled', 'false')
    expect(starToggle(list, 'Message 9')).toHaveAttribute('aria-pressed', 'false')
    expect(bodiesTo(fetchMock, FLAG)).toEqual([])
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
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Archive' }))
    await reported('polite', 'Archived 1 message.')
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Archive' }))
    await waitFor(() => {
      expect(report('polite')).toBeEmptyDOMElement()
    })
    secondArchive.resolve()
    await reported('polite', 'Archived 1 message.')
    expect(within(list).queryByRole('link', { name: /Message 8Preview/ })).not.toBeInTheDocument()
  })

  it('lists a folder that failed in several batches once', async () => {
    const many = (account: number, count: number): ReturnType<typeof messageItem>[] =>
      Array.from({ length: count }, (_, index) => messageItem(account, 'INBOX', 1000 - index))
    const work = many(WORK, 60)
    const home = many(HOME, 50)
    stubMailApi(
      state({
        messages: { '1/INBOX': work, '2/INBOX': home },
        searchResult: { messages: [...work, ...home], next_cursor: null, failures: [] },
        // The first batch is all work: refused whole; the second fails work's group in a 200.
        groupFailures: { '1/INBOX': 'stale_reference' },
      }),
    )
    renderMailApp('/mail?q=%7B%7D')

    await screen.findByRole('list', { name: 'Search results' })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all' }))
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Move to Trash' }))

    await reported('assertive', 'Moved 50 messages to Trash.')
    expect(within(report('assertive')).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      expect.stringContaining('work, Inbox: This message is out of date'),
    ])
  })

  it('sends a large selection 50 at a time, and reports a refused batch without stopping', async () => {
    const many = (account: number, folder: string, count: number): ReturnType<typeof messageItem>[] =>
      Array.from({ length: count }, (_, index) => messageItem(account, folder, 1000 - index))
    const work = many(WORK, 'INBOX', 50)
    const projects = many(WORK, 'Projects', 50)
    const home = many(HOME, 'INBOX', 10)
    const fetchMock = stubMailApi(
      state({
        messages: { '1/INBOX': work, '1/Projects': projects, '2/INBOX': home },
        searchResult: { messages: [...work, ...projects, ...home], next_cursor: null, failures: [] },
        // The second batch is all Projects: refused as a whole.
        groupFailures: { '1/Projects': 'stale_reference' },
      }),
    )
    renderMailApp('/mail?q=%7B%7D')

    await screen.findByRole('list', { name: 'Search results' })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all' }))
    expect(await selection()).toHaveTextContent('110 selected')
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Move to Trash' }))

    await reported('assertive', 'Moved 60 messages to Trash.')
    expect(report('assertive')).toHaveTextContent('work, Projects: This message is out of date')
    expect((bodiesTo(fetchMock, TRASH) as { refs: string[] }[]).map((body) => body.refs)).toEqual([
      work.map((item) => item.ref),
      projects.map((item) => item.ref),
      home.map((item) => item.ref),
    ])
    expect(await selection()).toHaveTextContent('50 selected')
  })

  it('starts a new folder from what it reads: no selection, no flags changed before', async () => {
    const fake = state({ messages: { '1/INBOX': inbox, '1/Projects': [] } })
    stubMailApi(fake)
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Mark as unread' }))
    expect(await within(list).findByRole('link', { name: /Unread:\s*Message 9Preview/ })).toBeInTheDocument()
    // Another client reads it meanwhile.
    fake.messages['1/INBOX'] = (fake.messages['1/INBOX'] ?? []).map((item) =>
      item.ref === second?.ref ? { ...item, flags: { ...item.flags, seen: true } } : item,
    )

    const folders = screen.getByRole('tree', { name: 'Folders of work' })
    await userEvent.click(within(folders).getByRole('treeitem', { name: 'Projects' }))
    expect(await screen.findByText('No messages in this folder.')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Selected messages' })).not.toBeInTheDocument()
    await userEvent.click(within(folders).getByRole('treeitem', { name: /^Inbox/ }))

    const again = await messagesList()
    expect(within(again).getByRole('checkbox', { name: 'Select Message 9' })).not.toBeChecked()
    expect(rowLink(again, 'Message 9')).not.toHaveAccessibleName(/Unread:/)
  })

  it('reads on, without calling the folder empty, once every loaded row moved away', async () => {
    const long = Array.from({ length: 26 }, (_, index) => messageItem(WORK, 'INBOX', 100 - index))
    stubMailApi(state({ messages: { '1/INBOX': long } }))
    renderMailApp('/mail')

    await messagesList()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all' }))
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Archive' }))
    await reported('polite', 'Archived 25 messages.')
    expect(screen.queryByText('No messages in this folder.')).not.toBeInTheDocument()
    expect(rowLink(await messagesList(), 'Message 75')).toBeInTheDocument()
  })

  it('goes on below the last loaded row once it moved away, showing no row twice', async () => {
    const long = Array.from({ length: 26 }, (_, index) => messageItem(WORK, 'INBOX', 100 - index))
    stubMailApi(state({ messages: { '1/INBOX': long } }))
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 76' }))
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Archive' }))
    await reported('polite', 'Archived 1 message.')

    scrollListToEnd()
    expect(await within(list).findByRole('link', { name: /Message 75Preview/ })).toBeInTheDocument()
    const subjects = within(list)
      .getAllByRole('link')
      .map((link) => /Message \d+/.exec(link.textContent)?.[0])
    expect(subjects).toEqual(long.filter((item) => item.subject !== 'Message 76').map((item) => item.subject))
  })

  it('hides Archive for an account without an Archive folder', async () => {
    stubMailApi(state())
    renderMailApp('/mail?account=2')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select From home' }))
    const group = await selection()
    expect(within(group).getByRole('button', { name: 'Move to Trash' })).toBeInTheDocument()
    expect(within(group).queryByRole('button', { name: 'Archive' })).not.toBeInTheDocument()
  })
})

describe('rows', () => {
  const STARRED = { seen: true, flagged: true, answered: false, forwarded: false, draft: false, to_sort: true }
  const ALICE: UserJson = {
    username: 'alice',
    is_admin: false,
    time_zone: null,
    mark_read_subfolders: true,
    index_max_age_days: null,
    conversations: true,
    undo_send_seconds: 10,
    appearance: { ...DEFAULT_APPEARANCE, density: 'compact' },
  }
  const box = (list: HTMLElement, subject: string): HTMLElement => within(list).getByRole('checkbox', { name: `Select ${subject}` })

  it('shows a checkbox on hover or focus until a row is selected, then on every row', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    expect(box(list, 'Message 9')).toHaveClass('opacity-0', 'group-hover/row:opacity-100', 'group-focus-within/row:opacity-100')
    // A touch screen neither hovers nor wants the width: no checkbox until one is selected.
    expect(box(list, 'Message 9')).toHaveClass('pointer-coarse:sr-only')
    expect(list).toHaveAttribute('data-density', 'comfortable')
    expect(within(list).getByText('Preview of 9')).toBeInTheDocument()
    await userEvent.click(box(list, 'Message 9'))
    await waitFor(() => {
      expect(box(list, 'Message 8')).not.toHaveClass('opacity-0')
    })
    expect(box(list, 'Message 8')).not.toHaveClass('pointer-coarse:sr-only')
    await userEvent.click(box(list, 'Message 9'))
    await waitFor(() => {
      expect(box(list, 'Message 8')).toHaveClass('opacity-0')
    })
  })

  it('shows a set star and To sort as marks by the date, the toggles over the row', async () => {
    stubMailApi(state({ messages: { '1/INBOX': [messageItem(WORK, 'INBOX', 3, { flags: STARRED }), ...inbox.slice(1)] } }))
    renderMailApp('/mail')

    const list = await messagesList()
    const marked = row(list, 'Message 3')
    expect(within(marked).getByRole('img', { name: 'Starred' })).toHaveClass('text-star')
    expect(within(marked).getByRole('img', { name: 'To sort' })).toBeInTheDocument()
    const toggles = within(marked).getByRole('button', { name: 'Star', pressed: true }).parentElement
    expect(toggles).toHaveClass('absolute', 'opacity-0', 'pointer-events-none')
    // Not on a touch screen, where they would crowd the row: the message's actions and a swipe do it.
    expect(toggles).toHaveClass('pointer-coarse:sr-only')
    expect(toggles).not.toHaveClass('pointer-coarse:opacity-100')
    // The date under them shows none of itself past their edge, but only where they show.
    const date = within(marked).getByRole('img', { name: 'Starred' }).nextElementSibling
    expect(date).toHaveClass('group-hover/row:text-transparent', 'group-focus-within/row:text-transparent', 'pointer-coarse:text-muted-foreground')
    expect(within(row(list, 'Message 9')).queryByRole('img', { name: 'Starred' })).not.toBeInTheDocument()
  })

  it('selects a row a finger is held on, opening nothing', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    const link = rowLink(list, 'Message 9')
    fireEvent.pointerDown(link, { pointerId: 1, pointerType: 'touch', clientX: 50, clientY: 20 })
    await waitFor(() => {
      expect(box(list, 'Message 9')).toBeChecked()
    })
    // The browser's own long press (a menu) does nothing, and the lifted finger's click opens nothing.
    expect(fireEvent.contextMenu(link)).toBe(false)
    fireEvent.pointerUp(link, { pointerId: 1, pointerType: 'touch' })
    fireEvent.click(link)
    expect(link).not.toHaveAttribute('aria-current')
    expect(box(list, 'Message 8')).not.toHaveClass('pointer-coarse:sr-only')
  })

  it('highlights the whole open row, its checkbox gutter too', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    expect(row(list, 'Message 9')).not.toHaveClass('bg-accent')
    await userEvent.click(rowLink(list, 'Message 9'))
    await waitFor(() => {
      expect(row(list, 'Message 9')).toHaveClass('bg-accent')
    })
    expect(row(list, 'Message 8')).not.toHaveClass('bg-accent')
  })

  it('leaves the opening out of a compact row', async () => {
    stubMailApi(state())
    renderMailApp('/mail', { user: ALICE })

    const list = await messagesList()
    expect(list).toHaveAttribute('data-density', 'compact')
    expect(within(list).getByRole('link', { name: /Message 9/ })).toBeInTheDocument()
    expect(within(list).queryByText('Preview of 9')).not.toBeInTheDocument()
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
    for (const alert of screen.getAllByRole('alert')) {
      expect(alert).toBeEmptyDOMElement()
    }
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

describe('live counts over guessed ones', () => {
  /** The fake's state, the first answer to `request` held back until the returned `release`. */
  function holding(request: string): { fake: FakeMailState; release: () => void } {
    const answer = deferred()
    let held = false
    const fake = state({
      hold: (each) => {
        if (each === request && !held) {
          held = true
          return answer.promise
        }
        return undefined
      },
    })
    return { fake, release: answer.resolve }
  }

  it('keeps the live count that arrived before the mark-read answered, instead of taking one off it again', async () => {
    stubEventSource()
    const { fake, release } = holding(`POST ${FLAG}`)
    const fetchMock = stubMailApi(fake)
    renderMailApp(`/mail?message=${String(unread?.ref)}`)
    const source = await openStream()
    const folders = await screen.findByRole('tree', { name: 'Folders of work' })
    await waitFor(() => {
      expect(bodiesTo(fetchMock, FLAG)).toHaveLength(1)
    })

    sendCounts(source, 'INBOX', 2, 5)
    expect(await within(folders).findByRole('treeitem', { name: 'Inbox, 2 unread' })).toBeInTheDocument()
    release()
    const list = await messagesList()
    await waitFor(() => {
      expect(within(list).queryByRole('link', { name: /Unread:/ })).not.toBeInTheDocument()
    })
    expect(within(folders).getByRole('treeitem', { name: 'Inbox, 2 unread' })).toBeInTheDocument()
  })

  it('takes the live count that arrives after the answer as it is', async () => {
    stubEventSource()
    stubMailApi(state())
    renderMailApp(`/mail?message=${String(unread?.ref)}`)
    const source = await openStream()
    const folders = await screen.findByRole('tree', { name: 'Folders of work' })

    expect(await within(folders).findByRole('treeitem', { name: 'Inbox, 2 unread' })).toBeInTheDocument()
    sendCounts(source, 'INBOX', 2, 5)
    expect(within(folders).getByRole('treeitem', { name: 'Inbox, 2 unread' })).toBeInTheDocument()
  })

  it('still guesses the folders no live count came for since the request started', async () => {
    stubEventSource()
    const { fake, release } = holding('POST /api/v1/mail/messages/archive')
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail')
    const list = await messagesList()
    const source = await openStream()
    const folders = await screen.findByRole('tree', { name: 'Folders of work' })

    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 10' }))
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Archive' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/archive')).toHaveLength(1)
    })
    sendCounts(source, 'Archive', 1, 3)
    release()

    await reported('polite', 'Archived 1 message.')
    // Archive as the server said, not one more; Inbox by the guess, as nothing live came for it.
    expect(within(folders).getByRole('treeitem', { name: 'Archive, 1 unread' })).toBeInTheDocument()
    expect(within(folders).getByRole('treeitem', { name: 'Inbox, 2 unread' })).toBeInTheDocument()
  })

  it('guesses again for a request started after the live count arrived', async () => {
    stubEventSource()
    stubMailApi(state())
    renderMailApp('/mail')
    const list = await messagesList()
    const source = await openStream()
    const folders = await screen.findByRole('tree', { name: 'Folders of work' })

    sendCounts(source, 'INBOX', 4, 6)
    expect(await within(folders).findByRole('treeitem', { name: 'Inbox, 4 unread' })).toBeInTheDocument()
    await userEvent.click(within(list).getByRole('link', { name: /Unread:\s*Message 10Preview/ }))
    expect(await within(folders).findByRole('treeitem', { name: 'Inbox, 3 unread' })).toBeInTheDocument()
  })
})

describe('message context menu', () => {
  it('acts on an unselected row alone, leaving the selection as it was', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 10' }))
    fireEvent.contextMenu(rowLink(list, 'Message 8'), { clientX: 40, clientY: 60 })
    const menu = screen.getByRole('menu', { name: 'Actions for Message 8' })
    expect(within(menu).getByRole('menuitem', { name: 'Reply' })).toHaveFocus()
    expect(within(menu).getByRole('menuitem', { name: 'Archive' })).toHaveAttribute('aria-keyshortcuts', 'e')
    await userEvent.click(within(menu).getByRole('menuitem', { name: 'Archive' }))

    await reported('polite', 'Archived 1 message.')
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/archive')).toEqual([{ refs: [third?.ref], source_folder: 'INBOX' }])
    expect(within(list).getByRole('checkbox', { name: 'Select Message 10' })).toBeChecked()
  })

  it('acts on the whole selection from a selected row, with no reply to several', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 10' }))
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    fireEvent.contextMenu(rowLink(list, 'Message 9'))
    const menu = screen.getByRole('menu')
    expect(within(menu).queryByRole('menuitem', { name: 'Reply' })).not.toBeInTheDocument()
    await userEvent.click(within(menu).getByRole('menuitem', { name: 'Star' }))

    await reported('polite', 'Starred 2 messages.')
    expect(bodiesTo(fetchMock, FLAG)).toEqual([{ refs: [unread?.ref, second?.ref], flagged: true }])
  })

  it('opens the move picker on an unselected row, which becomes the selection', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 10' }))
    fireEvent.contextMenu(rowLink(list, 'Message 8'))
    await userEvent.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: 'Move to…' }))

    expect(await screen.findByRole('dialog', { name: 'Move to' })).toBeInTheDocument()
    expect(within(list).getByRole('checkbox', { name: 'Select Message 8' })).toBeChecked()
    expect(within(list).getByRole('checkbox', { name: 'Select Message 10' })).not.toBeChecked()
  })

  it('leaves Shift+right-click to the browser', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    const shown = fireEvent.contextMenu(rowLink(list, 'Message 8'), { shiftKey: true })
    expect(shown).toBe(true)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('opens from the keyboard and closes back onto the row', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    const link = rowLink(list, 'Message 8')
    link.focus()
    await userEvent.keyboard('{Shift>}{F10}{/Shift}')
    expect(screen.getByRole('menu', { name: 'Actions for Message 8' })).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(link).toHaveFocus()
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
    expect(await selection()).toHaveTextContent('1 selected')
    await userEvent.keyboard('{Escape}')
    expect(within(list).getByRole('checkbox', { name: 'Select Message 9' })).not.toBeChecked()
  })

  it.each([
    ['e', '/api/v1/mail/messages/archive', { refs: [second?.ref], source_folder: 'INBOX' }],
    ['#', '/api/v1/mail/messages/trash', { refs: [second?.ref], source_folder: 'INBOX' }],
    ['s', '/api/v1/mail/messages/flag', { refs: [second?.ref], flagged: true }],
    ['t', '/api/v1/mail/messages/flag', { refs: [second?.ref], to_sort: true }],
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
    await selection()

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
      expect(bodiesTo(fetchMock, TRASH)).toEqual([{ refs: [second?.ref], source_folder: 'INBOX' }])
    })
  })

  it('works right after ticking a checkbox', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')
    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))

    await userEvent.keyboard('e')
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/archive')).toEqual([{ refs: [second?.ref], source_folder: 'INBOX' }])
    })
  })

  it('is ignored while typing, with Ctrl or Meta, with Alt on a letter and on a held key', async () => {
    const fetchMock = stubMailApi(state())
    const history = renderMailApp(`/mail?message=${String(second?.ref)}`)
    await actions()

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search terms' }), 'jkxes#uv')
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
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/archive')).toEqual([{ refs: [second?.ref], source_folder: 'INBOX' }])
  })
})

function renderAt(path: string): ReturnType<typeof renderMailApp> {
  stubMailApi(state())
  return renderMailApp(path)
}

describe("the open message's action bar", () => {
  it('has the replies, the triage actions and a More menu of the rest, all from the registry', async () => {
    stubMailApi(state())
    renderMailApp(`/mail?message=${String(second?.ref)}`)

    const bar = await actions()
    expect(within(bar).getAllByRole('button').map((button) => button.getAttribute('aria-label') ?? button.textContent)).toEqual([
      'Reply',
      'Reply all',
      'Forward',
      'Archive',
      'Move to Trash',
      'Move to…',
      'Move to another account…',
      'Star',
      'Mark To sort',
      'Mark as unread',
      'More actions',
    ])
    expect(within(bar).getByRole('button', { name: 'Archive' })).toHaveAttribute('title', 'Archive (e)')
    const more = within(bar).getByRole('button', { name: 'More actions' })
    await userEvent.click(more)
    const menu = screen.getByRole('menu', { name: 'More actions' })
    expect(within(menu).getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Print', 'Download .eml', 'Snooze'])
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(more).toHaveFocus()
  })

  it('acts on the open message, not on the rows selected, which keep their own toolbar', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp(`/mail?message=${String(second?.ref)}`)

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
    expect(await selection()).toHaveTextContent('1 selected')
    // The keys act on the selection now, so the bar's buttons do not name them.
    const archive = within(await actions()).getByRole('button', { name: 'Archive' })
    expect(archive).not.toHaveAttribute('aria-keyshortcuts')
    expect(archive).toHaveAttribute('title', 'Archive')
    expect(within(await selection()).getByRole('button', { name: 'Archive' })).toHaveAttribute('aria-keyshortcuts', 'e')
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Star' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, FLAG)).toEqual([{ refs: [second?.ref], flagged: true }])
    })
    expect(within(list).getByRole('checkbox', { name: 'Select Message 8' })).toBeChecked()
  })

  it('opens its own move picker beside a selection, Escape closing it and keeping the selection', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp(`/mail?message=${String(second?.ref)}`)

    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
    const move = within(await actions()).getByRole('button', { name: 'Move to…' })
    await userEvent.click(move)
    expect(move).toHaveAttribute('aria-expanded', 'true')
    expect(within(await selection()).getByRole('button', { name: 'Move to…' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getAllByRole('dialog', { name: 'Move to' })).toHaveLength(1)
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(move).toHaveFocus()
    expect(within(list).getByRole('checkbox', { name: 'Select Message 8' })).toBeChecked()

    await userEvent.click(move)
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Move to' })).getByRole('button', { name: 'Projects' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/move')).toEqual([
        { refs: [second?.ref], folder: 'Projects', source_folder: 'INBOX' },
      ])
    })
  })

  it('keeps to one row, the actions that do not fit going to the top of the More menu', async () => {
    // jsdom lays nothing out: every button 40 px wide, the bar `room` wide, its observer fired by hand.
    let room = 400
    const fits: (() => void)[] = []
    vi.stubGlobal(
      'ResizeObserver',
      class {
        private readonly fit: () => void
        constructor(fit: () => void) {
          this.fit = fit
        }
        observe(): void {
          fits.push(this.fit)
        }
        disconnect(): void {
          fits.splice(fits.indexOf(this.fit), 1)
        }
      },
    )
    const offsetWidth = vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(40)
    const clientWidth = vi.spyOn(Element.prototype, 'clientWidth', 'get').mockImplementation(function (this: Element) {
      return this.getAttribute('aria-label') === 'Message actions' ? room : 0
    })
    try {
      const fetchMock = stubMailApi(state())
      renderMailApp(`/mail?message=${String(second?.ref)}`)

      const bar = await actions()
      // Twelve buttons, the separator counted as one, are 480 px: To sort and Move to… go first.
      await waitFor(() => {
        expect(within(bar).getAllByRole('button').map((button) => button.getAttribute('aria-label') ?? button.textContent)).toEqual([
          'Reply',
          'Reply all',
          'Forward',
          'Archive',
          'Move to Trash',
          'Move to another account…',
          'Star',
          'Mark as unread',
          'More actions',
        ])
      })
      const more = within(bar).getByRole('button', { name: 'More actions' })
      await userEvent.click(more)
      const menu = screen.getByRole('menu', { name: 'More actions' })
      const items = within(menu).getAllByRole('menuitem')
      expect(items.map((item) => item.textContent)).toEqual(['Move to…v', 'Mark To sortt', 'Print', 'Download .eml', 'Snooze'])
      // Nothing is selected: the keys act on the open message, and the moved items name theirs.
      expect(items.map((item) => item.getAttribute('aria-keyshortcuts'))).toEqual([
        'v',
        't',
        null,
        null,
        null,
      ])
      // Chosen there, the move picker opens on the open message, and gives focus back to More.
      await userEvent.click(within(menu).getByRole('menuitem', { name: /Move to…/ }))
      const picker = await screen.findByRole('dialog', { name: 'Move to' })
      await userEvent.keyboard('{Escape}')
      expect(picker).not.toBeInTheDocument()
      expect(more).toHaveFocus()
      await userEvent.click(more)
      await userEvent.click(within(screen.getByRole('menu', { name: 'More actions' })).getByRole('menuitem', { name: /Mark To sort/ }))
      await waitFor(() => {
        expect(bodiesTo(fetchMock, FLAG)).toEqual([{ refs: [second?.ref], to_sort: true }])
      })

      // Room again: they come back to the bar.
      room = 1000
      act(() => {
        for (const fit of [...fits]) {
          fit()
        }
      })
      expect(within(bar).getByRole('button', { name: 'Move to…' })).toBeVisible()
    } finally {
      offsetWidth.mockRestore()
      clientWidth.mockRestore()
    }
  })

  it('shows the move picker v opens when nothing is selected', async () => {
    stubMailApi(state())
    renderMailApp(`/mail?message=${String(second?.ref)}`)

    const bar = await actions()
    await waitFor(() => {
      expect(within(bar).getByRole('button', { name: 'Move to…' })).toBeInTheDocument()
    })
    await userEvent.keyboard('v')
    expect(await screen.findByRole('dialog', { name: 'Move to' })).toBeInTheDocument()
    expect(within(bar).getByRole('button', { name: 'Move to…' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.queryByRole('group', { name: 'Selected messages' })).not.toBeInTheDocument()
  })
})

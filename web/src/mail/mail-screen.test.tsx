import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { bodiesTo, type FakeMailState, HOME, messageItem, messageView, queriesTo, refusal, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { onSessionEnded } from '@/api/http.ts'
import { expectNoAxeViolations } from '@/test/axe.ts'
import { scrollListToEnd } from '@/test/fake-virtualizer.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

import { HTML_SANDBOX } from './html-body.tsx'

const inbox = Array.from({ length: 30 }, (_, index) =>
  messageItem(WORK, 'INBOX', 100 - index, index === 0 ? { flags: { seen: false, flagged: true, answered: false, forwarded: false, draft: false, to_sort: false } } : {}),
)
const plain = inbox[1] ?? messageItem(WORK, 'INBOX', 0)
const html = inbox[2] ?? messageItem(WORK, 'INBOX', 0)
const htmlOnly = inbox[3] ?? messageItem(WORK, 'INBOX', 0)
const long = inbox[4] ?? messageItem(WORK, 'INBOX', 0)

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 3, total: 30 },
        { path: 'Sent Messages', role: 'sent', unread: 0, total: 4 },
        { path: 'Archive/2025', role: null, unread: 0, total: 2 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 1 }],
    },
    messages: {
      '1/INBOX': inbox,
      '1/Archive/2025': [messageItem(WORK, 'Archive/2025', 7, { subject: 'Old news' })],
      '2/INBOX': [messageItem(HOME, 'INBOX', 1, { subject: 'From home' })],
    },
    views: {
      [plain.ref]: {
        ...messageView(plain, {
          text: { content: 'Hello there.', removed_chars: 2, incomplete: false, next_offset: null, quoted_omitted: true },
          attachments: [{ part: '2', name: 'notes.pdf', type: 'application/pdf', size: 40211 }],
        }),
        quoted: {
          content: 'Hello there.\n> what came before',
          removed_chars: 2,
          incomplete: false,
          next_offset: null,
          quoted_omitted: false,
        },
      },
      [html.ref]: messageView(html, { has_html: true, remote_images: 3, loadable_remote_images: 2 }),
      [htmlOnly.ref]: messageView(htmlOnly, {
        has_html: true,
        text: { content: '', removed_chars: 0, incomplete: false, next_offset: null, quoted_omitted: false },
      }),
      [long.ref]: {
        ...messageView(long, {
          text: { content: 'First window.', removed_chars: 0, incomplete: false, next_offset: 13, quoted_omitted: true },
        }),
        quoted: {
          content: 'First window.> and what came before',
          removed_chars: 0,
          incomplete: false,
          next_offset: 13,
          quoted_omitted: false,
        },
      },
    },
    ...overrides,
  }
}

const messagesList = (): Promise<HTMLElement> => screen.findByRole('list', { name: 'Messages' })
/** The list pane: its own alerts, not the toolbar's live regions. */
const listRegion = (): Promise<HTMLElement> => screen.findByRole('region', { name: 'Messages' })

afterEach(() => {
  vi.unstubAllGlobals()
  onSessionEnded(null)
})

describe('MailScreen', () => {
  it('passes axe on the list, a text message and an HTML one', async () => {
    stubMailApi(state())
    renderMailApp('/mail')
    await screen.findByRole('tree', { name: 'Folders of work' })
    await messagesList()
    await expectNoAxeViolations()

    await userEvent.click(within(await messagesList()).getByRole('link', { name: /Message 99/ }))
    await screen.findByRole('region', { name: 'Message body' })
    await expectNoAxeViolations()

    await userEvent.click(within(await messagesList()).getByRole('link', { name: /Message 98/ }))
    await screen.findByRole('heading', { name: html.subject })
    await expectNoAxeViolations()
  })

  it("opens the first account's inbox, with folder roles and unread counts", async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const folders = await screen.findByRole('tree', { name: 'Folders of work' })
    const inboxLink = within(folders).getByRole('treeitem', { name: 'Inbox, 3 unread' })
    expect(inboxLink).toHaveAttribute('aria-current', 'page')
    expect(within(folders).getByRole('treeitem', { name: 'Sent' })).toHaveAttribute('title', 'Sent Messages')
    // Archive is a level the server did not list: not a link, closed until opened.
    const archive = within(folders).getByRole('treeitem', { name: 'Archive' })
    expect(archive.tagName).toBe('SPAN')
    expect(archive).toHaveAttribute('aria-expanded', 'false')
    expect(within(folders).queryByRole('treeitem', { name: '2025' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Account: work. Switch account' })).toBeInTheDocument()
    expect(screen.queryByRole('tree', { name: 'Folders of home' })).not.toBeInTheDocument()

    const list = await messagesList()
    expect(within(list).getAllByRole('listitem')).toHaveLength(25)
    expect(within(list).getByRole('link', { name: /Unread:\s*Message 100/ })).toBeInTheDocument()
    expect(within(list).getByRole('button', { name: 'Star', pressed: true })).toBeInTheDocument()
  })

  it('marks the rows of messages answered or forwarded', async () => {
    const flags = { seen: true, flagged: false, draft: false, to_sort: false }
    const messages = {
      '1/INBOX': [
        messageItem(WORK, 'INBOX', 3, { subject: 'Replied', flags: { ...flags, answered: true, forwarded: false } }),
        messageItem(WORK, 'INBOX', 2, { subject: 'Passed on', flags: { ...flags, answered: false, forwarded: true } }),
        messageItem(WORK, 'INBOX', 1, { subject: 'Untouched', flags: { ...flags, answered: false, forwarded: false } }),
      ],
    }
    stubMailApi(state({ messages }))
    renderMailApp('/mail')

    const list = await messagesList()
    const row = (subject: string) => within(list).getByRole('link', { name: new RegExp(subject) })
    expect(within(row('Replied')).getByRole('img', { name: 'Answered' })).toBeInTheDocument()
    expect(within(row('Replied')).queryByRole('img', { name: 'Forwarded' })).not.toBeInTheDocument()
    expect(within(row('Passed on')).getByRole('img', { name: 'Forwarded' })).toBeInTheDocument()
    expect(within(row('Untouched')).queryByRole('img', { name: /Answered|Forwarded/ })).not.toBeInTheDocument()
  })

  it('names each account by its name, with its colour, never by its id', async () => {
    stubMailApi(state({ names: { [WORK]: { name: 'Work mail', colour: 'teal' }, [HOME]: { name: 'Home', colour: null } } }))
    renderMailApp('/mail')

    expect(await screen.findByRole('tree', { name: 'Folders of Work mail' })).toBeInTheDocument()
    const sidebar = screen.getByRole('navigation', { name: 'Accounts and folders' })
    expect(within(sidebar).getByRole('button', { name: 'Account: Work mail. Switch account' })).toBeInTheDocument()
    expect(within(sidebar).getByRole('button', { name: 'New folder in Work mail' })).toBeInTheDocument()
    await userEvent.click(within(sidebar).getByRole('button', { name: /^Account: Work mail/ }))
    const menu = screen.getByRole('menu', { name: 'Accounts' })
    expect(within(menu).getByRole('menuitemcheckbox', { name: 'Work mail' })).toHaveAttribute('aria-checked', 'true')
    expect(within(menu).getByRole('menuitemcheckbox', { name: 'Home' })).toHaveAttribute('aria-checked', 'false')
    await userEvent.keyboard('{Escape}')
    expect(within(sidebar).queryByText('work')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'More options' }))
    const accounts = within(screen.getByLabelText('Account')).getAllByRole('option')
    expect(accounts.map((option) => [option.getAttribute('value'), option.textContent])).toEqual([
      ['*', 'All accounts'],
      ['1', 'Work mail'],
      ['2', 'Home'],
    ])
  })

  it('reads the next page with the cursor the last one gave as the list nears its end, until there is none', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    expect(queriesTo(fetchMock, '/api/v1/mail/accounts/1/messages')).toHaveLength(1)
    scrollListToEnd()
    expect(await within(list).findByRole('link', { name: /Message 71/ })).toBeInTheDocument()
    expect(within(list).getAllByRole('listitem')).toHaveLength(30)
    expect(within(list).getAllByRole('listitem').at(-1)).toHaveAttribute('aria-setsize', '30')
    scrollListToEnd()
    const cursors = queriesTo(fetchMock, '/api/v1/mail/accounts/1/messages').map((query) => query.get('cursor'))
    expect(cursors).toEqual([null, 'before:76'])
  })

  it('words a page that fails to load more, and keeps what was shown', async () => {
    const fake = state()
    stubMailApi(fake)
    renderMailApp('/mail')

    const list = await messagesList()
    fake.refuse = { 'GET /api/v1/mail/accounts/1/messages': refusal(504, 'timeout') }
    scrollListToEnd()
    expect(await within(await listRegion()).findByRole('alert')).toHaveTextContent('The mail server did not answer in time.')
    expect(within(list).getAllByRole('listitem')).toHaveLength(25)

    delete fake.refuse
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await within(list).findByRole('link', { name: /Message 71/ })).toBeInTheDocument()
    expect(within(await listRegion()).queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows more of a long text from next_offset, with the quoted choice it was read with', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp(`/mail?message=${long.ref}`)

    const body = await screen.findByRole('region', { name: 'Message body' })
    await userEvent.click(within(body).getByRole('button', { name: 'Show quoted text' }))
    await screen.findByRole('button', { name: 'Hide quoted text' })
    await userEvent.click(screen.getByRole('button', { name: 'Show more' }))
    expect(await screen.findByText('First window.> and what came before> and what came before')).toBeInTheDocument()
    const reads = queriesTo(fetchMock, `/api/v1/mail/messages/${long.ref}`)
    expect(reads.map((query) => [query.get('offset'), query.get('include_quoted')])).toEqual([
      [null, null],
      [null, 'true'],
      ['13', 'true'],
    ])
  })

  it('words a window that fails to load, and keeps the text shown', async () => {
    const fake = state()
    stubMailApi(fake)
    renderMailApp(`/mail?message=${long.ref}`)

    expect(await screen.findByText('First window.')).toBeInTheDocument()
    fake.refuse = { [`GET /api/v1/mail/messages/${long.ref}`]: refusal(429, 'busy') }
    await userEvent.click(screen.getByRole('button', { name: 'Show more' }))
    const body = screen.getByRole('region', { name: 'Message body' })
    expect(await within(body).findByRole('alert')).toHaveTextContent('The account is busy with another request.')
    expect(within(body).getByText('First window.')).toBeInTheDocument()
  })

  it('switches folder and account from the sidebar, and keeps them in the URL', async () => {
    stubMailApi(state())
    const history = renderMailApp('/mail')

    const folders = await screen.findByRole('tree', { name: 'Folders of work' })
    await userEvent.click(within(folders).getByRole('treeitem', { name: 'Archive' }))
    await userEvent.click(within(folders).getByRole('treeitem', { name: '2025' }))
    expect(await screen.findByRole('link', { name: /Old news/ })).toBeInTheDocument()
    expect(history.location.search).toContain('folder=Archive%2F2025')
    expect(within(folders).getByRole('treeitem', { name: '2025' })).toHaveAttribute('aria-current', 'page')

    await userEvent.click(screen.getByRole('button', { name: /^Account: work/ }))
    await userEvent.click(screen.getByRole('menuitemcheckbox', { name: 'home' }))
    expect(await screen.findByRole('link', { name: /From home/ })).toBeInTheDocument()
    expect(await screen.findByRole('tree', { name: 'Folders of home' })).toBeInTheDocument()
    expect(screen.queryByRole('tree', { name: 'Folders of work' })).not.toBeInTheDocument()
  })

  it('switches account from the keyboard, and opens that account again on this device', async () => {
    stubMailApi(state())
    const user = userEvent.setup()
    const first = renderMailApp('/mail')

    const switcher = await screen.findByRole('button', { name: /^Account: work/ })
    switcher.focus()
    await user.keyboard('{Enter}')
    expect(screen.getByRole('menuitemcheckbox', { name: 'work' })).toHaveFocus()
    await user.keyboard('{ArrowDown}{Enter}')
    expect(await screen.findByRole('tree', { name: 'Folders of home' })).toBeInTheDocument()
    expect(first.location.search).toContain('account=2')
    cleanup()

    renderMailApp('/mail')
    expect(await screen.findByRole('tree', { name: 'Folders of home' })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: /From home/ })).toBeInTheDocument()
  })

  it('opens the first account when the one remembered is gone', async () => {
    window.localStorage.setItem('mailroom.ui.mail.account', '99')
    stubMailApi(state())
    renderMailApp('/mail')

    expect(await screen.findByRole('tree', { name: 'Folders of work' })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: new RegExp(plain.subject) })).toBeInTheDocument()
  })

  it('neither answers nor follows another tab switching account', async () => {
    const anotherTab = (): void => {
      window.localStorage.setItem('mailroom.ui.mail.account', '2')
      fireEvent(window, new StorageEvent('storage', { key: 'mailroom.ui.mail.account', newValue: '2' }))
    }
    stubMailApi(state())
    renderMailApp('/mail?account=1')
    expect(await screen.findByRole('tree', { name: 'Folders of work' })).toBeInTheDocument()
    expect(window.localStorage.getItem('mailroom.ui.mail.account')).toBe('1')
    anotherTab()
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(window.localStorage.getItem('mailroom.ui.mail.account')).toBe('2')
    cleanup()

    window.localStorage.setItem('mailroom.ui.mail.account', '1')
    renderMailApp('/mail')
    expect(await screen.findByRole('tree', { name: 'Folders of work' })).toBeInTheDocument()
    anotherTab()
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(screen.getByRole('tree', { name: 'Folders of work' })).toBeInTheDocument()
    expect(screen.queryByRole('tree', { name: 'Folders of home' })).not.toBeInTheDocument()
  })

  it('leads back from an account that is gone', async () => {
    stubMailApi(state())
    const history = renderMailApp('/mail?account=99')

    await userEvent.click(await screen.findByRole('button', { name: /^Account: work/ }))
    expect(screen.getByRole('menuitemcheckbox', { name: 'work' })).not.toBeChecked()
    await userEvent.click(screen.getByRole('menuitemcheckbox', { name: 'work' }))
    expect(await screen.findByRole('tree', { name: 'Folders of work' })).toBeInTheDocument()
    expect(history.location.search).toContain('account=1')
  })

  it('links the only account when the URL names one that is gone', async () => {
    stubMailApi(state({ accounts: [WORK] }))
    renderMailApp('/mail?account=99')

    await userEvent.click(await screen.findByRole('link', { name: 'work' }))
    expect(await screen.findByRole('tree', { name: 'Folders of work' })).toBeInTheDocument()
  })

  it('keeps a view open when the account changes, listing the other account\'s folders', async () => {
    stubMailApi(state())
    const first = renderMailApp('/mail?view=all_inboxes')

    await userEvent.click(await screen.findByRole('button', { name: /^Account: work/ }))
    await userEvent.click(screen.getByRole('menuitemcheckbox', { name: 'home' }))
    expect(await screen.findByRole('tree', { name: 'Folders of home' })).toBeInTheDocument()
    expect(first.location.search).toContain('view=')
    expect(first.location.search).toContain('account=2')
  })

  it('shows nested folders as a tree that opens, closes and remembers', async () => {
    const folders = {
      [WORK]: [
        { path: 'INBOX', role: 'inbox' as const, unread: 1, total: 1 },
        { path: 'Lists', role: null, unread: 0, total: 0, selectable: false },
        { path: 'Lists/Rust', role: null, unread: 2, total: 3 },
        { path: 'Lists/Rust/Async', role: null, unread: 5, total: 5 },
        { path: 'Projects', role: null, unread: 0, total: 0 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox' as const, unread: 0, total: 1 }],
    }
    const messages = { ...state().messages, '1/Lists/Rust': [] }
    stubMailApi(state({ folders, messages }))
    const history = renderMailApp('/mail')

    const tree = await screen.findByRole('tree', { name: 'Folders of work' })
    // A closed level counts the unread of everything under it.
    const lists = within(tree).getByRole('treeitem', { name: 'Lists, 7 unread' })
    expect(lists).toHaveAttribute('aria-level', '1')
    expect(lists).not.toHaveAttribute('href')
    expect(within(tree).getAllByRole('treeitem').map((item) => item.getAttribute('data-path'))).toEqual([
      'INBOX',
      'Lists',
      'Projects',
    ])

    // ↓ and ↑ move, → opens and then goes in, ← closes and then goes up.
    const inbox = within(tree).getByRole('treeitem', { name: 'Inbox, 1 unread' })
    expect(inbox).toHaveAttribute('tabindex', '0')
    inbox.focus()
    await userEvent.keyboard('{ArrowDown}')
    expect(lists).toHaveFocus()
    await userEvent.keyboard('{ArrowRight}')
    expect(lists).toHaveAttribute('aria-expanded', 'true')
    expect(lists).toHaveAccessibleName('Lists')
    await userEvent.keyboard('{ArrowRight}')
    const rust = within(tree).getByRole('treeitem', { name: 'Rust, 7 unread' })
    expect(rust).toHaveFocus()
    expect(rust).toHaveAttribute('aria-level', '2')
    await userEvent.keyboard('{ArrowRight}{ArrowRight}')
    expect(within(tree).getByRole('treeitem', { name: 'Async, 5 unread' })).toHaveFocus()
    await userEvent.keyboard('{ArrowLeft}')
    expect(rust).toHaveFocus()
    await userEvent.keyboard('{ArrowLeft}{ArrowLeft}')
    expect(lists).toHaveFocus()
    await userEvent.keyboard('{End}')
    expect(within(tree).getByRole('treeitem', { name: 'Projects' })).toHaveFocus()
    await userEvent.keyboard('{Home}{ArrowDown}{Enter}')
    expect(lists).toHaveAttribute('aria-expanded', 'false')
    await userEvent.keyboard('{Enter}{ArrowDown}{Enter}')
    expect(await screen.findByText('No messages in this folder.')).toBeInTheDocument()
    expect(history.location.search).toContain('folder=Lists%2FRust')

    // Another visit opens the levels as they were left.
    cleanup()
    stubMailApi(state({ folders, messages }))
    renderMailApp('/mail')
    const again = await screen.findByRole('tree', { name: 'Folders of work' })
    expect(within(again).getByRole('treeitem', { name: 'Lists' })).toHaveAttribute('aria-expanded', 'true')
    expect(within(again).getByRole('treeitem', { name: 'Rust, 7 unread' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('lists a folder with its subfolders when asked, each row naming its folder, and remembers it', async () => {
    const folders = {
      [WORK]: [
        { path: 'INBOX', role: 'inbox' as const, unread: 1, total: 1 },
        { path: 'INBOX/Lists', role: null, unread: 2, total: 1 },
        { path: 'INBOX/Trash', role: 'trash' as const, unread: 4, total: 0 },
        { path: 'Projects', role: null, unread: 0, total: 0 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox' as const, unread: 0, total: 1 }],
    }
    const messages = {
      '1/INBOX': [messageItem(WORK, 'INBOX', 1, { subject: 'Hello' })],
      '1/INBOX/Lists': [messageItem(WORK, 'INBOX/Lists', 2, { subject: 'Release notes' })],
      '1/Projects': [],
    }
    const fetchMock = stubMailApi(state({ folders, messages }))
    const history = renderMailApp('/mail')

    const tree = await screen.findByRole('tree', { name: 'Folders of work' })
    const inbox = within(tree).getByRole('treeitem', { name: 'Inbox, 1 unread' })
    inbox.focus()
    await userEvent.keyboard('{Shift>}{F10}{/Shift}')
    const include = within(screen.getByRole('menu')).getByRole('menuitemcheckbox', { name: "Show subfolders' mail here" })
    expect(include).toHaveAttribute('aria-checked', 'false')
    expect(within(tree).getByTitle("Show subfolders' mail here")).toBeInTheDocument()
    await userEvent.click(include)

    const list = await screen.findByRole('list', { name: 'Messages' })
    expect(await within(list).findByRole('link', { name: /Release notes/ })).toHaveTextContent('INBOX/Lists')
    expect(within(list).getByRole('link', { name: /Hello/ })).toHaveTextContent(/INBOX$/)
    expect(history.location.search).toContain('sub=true')
    expect(queriesTo(fetchMock, '/api/v1/mail/accounts/1/messages').map((query) => query.get('subfolders'))).toEqual([null, 'true'])
    // Its count is what the list reads: Trash under it is left out.
    expect(within(tree).getByRole('treeitem', { name: 'Inbox, with subfolders, 3 unread' })).toBeInTheDocument()
    expect(within(tree).getByTitle('Show this folder only')).toBeInTheDocument()

    // A search in the folder reads its subfolders too; one in every folder does not.
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search terms' }), 'notes')
    await userEvent.click(screen.getByRole('button', { name: 'More options' }))
    expect(screen.getByLabelText('Folder')).toHaveDisplayValue('Inbox and its subfolders')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/search')).toEqual([{ account: WORK, folder: 'INBOX', text: 'notes', subfolders: true }])
    })
    await userEvent.click(screen.getByRole('button', { name: 'Clear search' }))

    // A folder without subfolders to read offers no such choice.
    within(tree).getByRole('treeitem', { name: 'Projects' }).focus()
    await userEvent.keyboard('{Shift>}{F10}{/Shift}')
    expect(within(screen.getByRole('menu')).queryByRole('menuitemcheckbox')).not.toBeInTheDocument()
    await userEvent.keyboard('{Escape}')

    // Another visit lists it as it was left, until it is turned off.
    cleanup()
    const again = stubMailApi(state({ folders, messages }))
    renderMailApp('/mail')
    const tree2 = await screen.findByRole('tree', { name: 'Folders of work' })
    expect(await within(tree2).findByRole('treeitem', { name: 'Inbox, with subfolders, 3 unread' })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: /Release notes/ })).toBeInTheDocument()
    expect(queriesTo(again, '/api/v1/mail/accounts/1/messages').map((query) => query.get('subfolders'))).toEqual(['true'])
    within(tree2).getByRole('treeitem', { name: /^Inbox/ }).focus()
    await userEvent.keyboard('{Shift>}{F10}{/Shift}')
    const checked = within(screen.getByRole('menu')).getByRole('menuitemcheckbox', { name: "Show subfolders' mail here" })
    expect(checked).toHaveAttribute('aria-checked', 'true')
    await userEvent.click(checked)
    expect(await within(tree2).findByRole('treeitem', { name: 'Inbox, 1 unread' })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('link', { name: /Release notes/ })).not.toBeInTheDocument()
    })
  })

  it('lists a folder alone once it has no subfolders to read, whatever the URL asks', async () => {
    const folders = {
      [WORK]: [
        { path: 'INBOX', role: 'inbox' as const, unread: 0, total: 0 },
        { path: 'Projects', role: null, unread: 0, total: 1 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox' as const, unread: 0, total: 0 }],
    }
    const messages = { '1/Projects': [messageItem(WORK, 'Projects', 1, { subject: 'Plan' })] }
    const fetchMock = stubMailApi(state({ folders, messages }))
    renderMailApp('/mail?account=1&folder=Projects&sub=true')

    const tree = await screen.findByRole('tree', { name: 'Folders of work' })
    await waitFor(() => {
      expect(queriesTo(fetchMock, '/api/v1/mail/accounts/1/messages').at(-1)?.get('subfolders')).toBeNull()
    })
    expect(within(tree).getByRole('treeitem', { name: 'Projects' })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: /Plan/ })).not.toHaveTextContent('Projects')
  })

  it('shows a text message as text, with its attachments, and no iframe', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    await userEvent.click(within(await messagesList()).getByRole('link', { name: /Message 99/ }))
    const body = await screen.findByRole('region', { name: 'Message body' })
    expect(within(body).getByText('Hello there.')).toBeInTheDocument()
    expect(screen.getByText('2 invisible characters were removed from this message.')).toBeInTheDocument()
    expect(document.querySelector('iframe')).toBeNull()
    expect(screen.queryByRole('button', { name: 'HTML' })).not.toBeInTheDocument()
    const attachment = screen.getByRole('link', { name: 'notes.pdf' })
    expect(attachment).toHaveAttribute('href', `/api/v1/mail/messages/${plain.ref}/parts/2`)
    // Saved, named by the API's Content-Disposition rather than by the sender's name here.
    expect(attachment).toHaveAttribute('download', '')

    expect(within(body).getByText('Quoted text or a signature is hidden.')).toBeInTheDocument()
    await userEvent.click(within(body).getByRole('button', { name: 'Show quoted text' }))
    expect(await screen.findByText(/what came before/)).toBeInTheDocument()
    expect(screen.queryByText('Quoted text or a signature is hidden.')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Hide quoted text' })).toBeInTheDocument()
    const reads = queriesTo(fetchMock, `/api/v1/mail/messages/${plain.ref}`)
    expect(reads.map((query) => query.get('include_quoted'))).toEqual([null, 'true'])
  })

  it('offers quoted text only when something was left out', async () => {
    const fake = state()
    // Its sender is read as text, so the text opens.
    fake.views[html.ref] = messageView(html, { has_html: true, preferred_view: 'text' })
    stubMailApi(fake)
    renderMailApp(`/mail?message=${html.ref}`)

    expect(await screen.findByText(`Body of ${html.subject}`)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Show quoted text' })).not.toBeInTheDocument()
    expect(screen.queryByText('Quoted text or a signature is hidden.')).not.toBeInTheDocument()
  })

  it("warns of a sender's name that names another address, quoting the real one, and shows the checks", async () => {
    const people = Array.from({ length: 4 }, (_, index) => ({ name: '', address: `p${String(index)}@example.org` }))
    const fake = state()
    fake.views[plain.ref] = messageView(plain, {
      from: { name: 'service@paypal.com', address: 'x@evil.example' },
      to: [...people, { name: 'Me', address: 'me@example.org' }],
      cc: [{ name: '', address: 'boss@example.org' }],
      sender_mismatch: true,
      sent_to: 'me@example.org',
      authentication: {
        verified: true,
        spf: 'pass',
        spf_domain: 'evil.example',
        dkim: null,
        dkim_domain: null,
        dmarc: 'fail',
        dmarc_pass: false,
        from_domain: null,
      },
    })
    stubMailApi(fake)
    renderMailApp(`/mail?message=${plain.ref}`)

    expect(
      await screen.findByText('The sender\'s name shows another address or site. It was sent from x@evil.example.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Failed: the sender may be forged')).toBeInTheDocument()
    expect(screen.getByText('SPF pass (evil.example), DKIM none, DMARC fail')).toBeInTheDocument()
    expect(screen.getByText('Sent to').nextElementSibling).toHaveTextContent('me@example.org')

    expect(screen.getByText(/You and 5 others/)).toBeInTheDocument()
    expect(screen.queryByText(/p0@example\.org/)).not.toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: 'Show all recipients', expanded: false })
    await userEvent.click(toggle)
    expect(screen.getByText(/p0@example\.org, .*Me <me@example\.org>/)).toBeInTheDocument()
    expect(screen.getByText('boss@example.org')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show fewer recipients', expanded: true })).toBe(toggle)
    expect(toggle).toHaveFocus()
    await userEvent.click(toggle)
    expect(screen.getByText(/You and 5 others/)).toBeInTheDocument()
    expect(toggle).toHaveFocus()
  })

  it("does not show a DMARC pass that is not for this message's From as a pass", async () => {
    const fake = state()
    fake.views[plain.ref] = messageView(plain, {
      authentication: {
        verified: true,
        spf: 'pass',
        spf_domain: null,
        dkim: 'pass',
        dkim_domain: 'evil.example',
        dmarc: 'pass',
        dmarc_pass: false,
        from_domain: null,
      },
    })
    stubMailApi(fake)
    renderMailApp(`/mail?message=${plain.ref}`)

    expect(await screen.findByText('SPF pass, DKIM pass (evil.example), DMARC pass, but not for this From')).toBeInTheDocument()
    expect(screen.queryByText('Verified: sent by example.org')).not.toBeInTheDocument()
  })

  it('shows a verified sender, and one not verified as such', async () => {
    const fake = state()
    fake.views[plain.ref] = messageView(plain, {
      authentication: {
        verified: true,
        spf: 'pass',
        spf_domain: 'example.org',
        dkim: 'pass',
        dkim_domain: 'example.org',
        dmarc: 'pass',
        dmarc_pass: true,
        from_domain: 'example.org',
      },
    })
    stubMailApi(fake)
    renderMailApp(`/mail?message=${plain.ref}`)

    expect(await screen.findByText('Verified: sent by example.org')).toBeInTheDocument()
    expect(screen.queryByText(/The sender's name/)).not.toBeInTheDocument()
    expect(screen.queryByText('Sent to')).not.toBeInTheDocument()
    const headers = screen.getByRole('heading', { name: plain.subject }).nextElementSibling as HTMLElement
    expect(within(headers).getByText('To').nextElementSibling).toHaveTextContent('me@example.org')
    cleanup()

    stubMailApi(state())
    renderMailApp(`/mail?message=${html.ref}`)
    expect(await screen.findByText('Not verified')).toBeInTheDocument()
    expect(screen.queryByText(/^SPF/)).not.toBeInTheDocument()
  })

  it("leaves out the authentication of the user's own mail, in the Sent folder", async () => {
    const sent = messageItem(WORK, 'Sent Messages', 3, { subject: 'My report' })
    const fake = state()
    fake.messages['1/Sent Messages'] = [sent]
    fake.views[sent.ref] = messageView(sent)
    stubMailApi(fake)
    renderMailApp(`/mail?message=${sent.ref}`)

    expect(await screen.findByRole('heading', { name: 'My report' })).toBeInTheDocument()
    await screen.findByRole('treeitem', { name: 'Sent' })
    expect(screen.queryByText('Authentication')).not.toBeInTheDocument()
    expect(screen.queryByText('Not verified')).not.toBeInTheDocument()
  })

  it('words a refused HTML view in place of the frame, never inside it', async () => {
    stubMailApi(state({ refuse: { [`GET /api/v1/mail/messages/${html.ref}/html`]: refusal(429, 'busy') } }))
    renderMailApp(`/mail?message=${html.ref}`)

    await userEvent.click(await screen.findByRole('button', { name: 'HTML' }))
    const body = screen.getByRole('region', { name: 'Message body' })
    expect(await within(body).findByRole('alert')).toHaveTextContent('The account is busy with another request.')
    expect(document.querySelector('iframe')).toBeNull()
    expect(within(body).getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('words an HTML view refused without a code (a proxy page) in the reader\'s language', async () => {
    stubMailApi(
      state({ refuse: { [`GET /api/v1/mail/messages/${html.ref}/html`]: { status: 502, body: '<html>Bad gateway</html>' } } }),
    )
    renderMailApp(`/mail?message=${html.ref}`)

    await userEvent.click(await screen.findByRole('button', { name: 'HTML' }))
    const body = screen.getByRole('region', { name: 'Message body' })
    expect(await within(body).findByRole('alert')).toHaveTextContent('Could not load the HTML version.')
    expect(document.querySelector('iframe')).toBeNull()
  })

  it('offers the original colours only beside a framed dark document, never beside an HTML error', async () => {
    document.documentElement.classList.add('dark')
    try {
      const fake = state({ refuse: { [`GET /api/v1/mail/messages/${html.ref}/html`]: refusal(429, 'busy') } })
      fake.views[html.ref] = messageView(html, { has_html: true, preferred_view: 'html' })
      stubMailApi(fake)
      renderMailApp(`/mail?message=${html.ref}`)

      const body = await screen.findByRole('region', { name: 'Message body' })
      expect(await within(body).findByRole('alert')).toHaveTextContent('The account is busy with another request.')
      expect(within(body).getByRole('button', { name: 'Try again' })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Original colours' })).not.toBeInTheDocument()

      // Once the document answers and is framed, the toggle is there.
      delete fake.refuse?.[`GET /api/v1/mail/messages/${html.ref}/html`]
      await userEvent.click(within(body).getByRole('button', { name: 'Try again' }))
      expect(await screen.findByTitle(`HTML version of ${html.subject}`)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Original colours' })).toHaveAttribute('aria-pressed', 'false')
    } finally {
      document.documentElement.classList.remove('dark')
    }
  })

  it('ends the session when the HTML view answers 401', async () => {
    const ended = vi.fn()
    onSessionEnded(ended)
    stubMailApi(state({ refuse: { [`GET /api/v1/mail/messages/${html.ref}/html`]: { status: 401, body: { detail: 'Not signed in.' } } } }))
    renderMailApp(`/mail?message=${html.ref}`)

    await userEvent.click(await screen.findByRole('button', { name: 'HTML' }))
    await waitFor(() => {
      expect(ended).toHaveBeenCalledTimes(1)
    })
  })

  it('opens HTML mail as HTML, and remembers a switch for its sender', async () => {
    const fake = state()
    const later = inbox[5] ?? messageItem(WORK, 'INBOX', 0)
    fake.views[html.ref] = messageView(html, { has_html: true })
    fake.views[later.ref] = messageView(later, { from: html.from, has_html: true })
    const fetchMock = stubMailApi(fake)
    renderMailApp(`/mail?message=${html.ref}`)

    expect(await screen.findByTitle(`HTML version of ${html.subject}`)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'HTML' })).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(screen.getByRole('button', { name: 'Text' }))
    expect(await screen.findByText(`Body of ${html.subject}`)).toBeInTheDocument()
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/sender-views')).toEqual([{ ref: html.ref, view: 'text' }])
    })

    // Another message from the same sender opens as text; HTML there forgets the sender.
    await userEvent.click(within(await messagesList()).getByRole('link', { name: new RegExp(later.subject) }))
    expect(await screen.findByText(`Body of ${later.subject}`)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'HTML' }))
    expect(await screen.findByTitle(`HTML version of ${later.subject}`)).toBeInTheDocument()
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/sender-views')).toEqual([
        { ref: html.ref, view: 'text' },
        { ref: later.ref, view: 'html' },
      ])
    })
  })

  it('says when a switch could not be remembered, and keeps it for this message', async () => {
    const fake = state({ refuse: { 'POST /api/v1/mail/sender-views': refusal(502, 'unavailable') } })
    fake.views[html.ref] = messageView(html, { has_html: true })
    stubMailApi(fake)
    renderMailApp(`/mail?message=${html.ref}`)

    await userEvent.click(await screen.findByRole('button', { name: 'Text' }))
    expect(await screen.findByText(`Body of ${html.subject}`)).toBeInTheDocument()
    const article = screen.getByRole('heading', { name: html.subject }).closest('article')
    expect(await within(article ?? document.body).findByRole('alert')).toHaveTextContent(/\S/)
  })

  it('shows HTML only in an iframe of the HTML document, sandboxed without scripts or same origin', async () => {
    stubMailApi(state())
    renderMailApp(`/mail?message=${html.ref}`)

    const frame = await screen.findByTitle(`HTML version of ${html.subject}`)
    expect(frame.tagName).toBe('IFRAME')
    expect(frame.getAttribute('sandbox')).toBe('allow-popups allow-popups-to-escape-sandbox')
    expect(HTML_SANDBOX).not.toMatch(/allow-scripts|allow-same-origin/)
    expect(frame).toHaveAttribute('referrerpolicy', 'no-referrer')
    expect(frame).toHaveAttribute('src', `/api/v1/mail/messages/${html.ref}/html`)
    expect(frame).not.toHaveAttribute('srcdoc')
  })

  it('shows HTML in dark colours in a dark theme, with the original colours a fragment away for that message', async () => {
    document.documentElement.classList.add('dark')
    try {
      const fake = state()
      fake.views[html.ref] = messageView(html, { has_html: true, preferred_view: 'html' })
      const fetchMock = stubMailApi(fake)
      renderMailApp(`/mail?message=${html.ref}`)

      const url = `/api/v1/mail/messages/${html.ref}/html`
      const frame = await screen.findByTitle(`HTML version of ${html.subject}`)
      expect(frame).toHaveAttribute('src', `${url}?dark=1#mailroom-dark`)
      const original = screen.getByRole('button', { name: 'Original colours' })
      expect(original).toHaveAttribute('aria-pressed', 'false')
      expect(original).toHaveAttribute('data-variant', 'outline')
      expect(original.querySelector('svg')).toBeNull()
      const htmlRequests = (): number =>
        fetchMock.mock.calls.filter(([input]) => String(input).includes(`${html.ref}/html`)).length
      const requested = htmlRequests()
      // The same document, its fragment alone switched: no request, the same frame.
      await userEvent.click(original)
      expect(original).toHaveAttribute('aria-pressed', 'true')
      expect(original).toHaveAttribute('data-variant', 'secondary')
      // A check marks it pressed, more than the secondary variant does in a dark theme; the name stays.
      expect(original.querySelector('svg.lucide-check')).toHaveAttribute('aria-hidden', 'true')
      expect(screen.getByTitle(`HTML version of ${html.subject}`)).toBe(frame)
      expect(frame).toHaveAttribute('src', `${url}?dark=1#mailroom-original`)
      expect(htmlRequests()).toBe(requested)
      // The choice outlives a look at the text, as long as the message is open.
      await userEvent.click(screen.getByRole('button', { name: 'Text' }))
      await userEvent.click(await screen.findByRole('button', { name: 'HTML' }))
      expect(await screen.findByTitle(`HTML version of ${html.subject}`)).toHaveAttribute(
        'src',
        `${url}?dark=1#mailroom-original`,
      )
      expect(screen.getByRole('button', { name: 'Original colours' })).toHaveAttribute('aria-pressed', 'true')
      await userEvent.click(screen.getByRole('button', { name: 'Original colours' }))
      expect(screen.getByTitle(`HTML version of ${html.subject}`)).toHaveAttribute('src', `${url}?dark=1#mailroom-dark`)
      expect(screen.getByRole('button', { name: 'Original colours' }).querySelector('svg')).toBeNull()

      // The theme turning light follows at once, and the toggle goes with it.
      await act(async () => {
        document.documentElement.classList.remove('dark')
        await Promise.resolve()
      })
      await waitFor(() => {
        expect(screen.getByTitle(`HTML version of ${html.subject}`)).toHaveAttribute('src', url)
      })
      expect(screen.queryByRole('button', { name: 'Original colours' })).not.toBeInTheDocument()
    } finally {
      document.documentElement.classList.remove('dark')
    }
  })

  it('reveals remote images for that message only, never remembered', async () => {
    stubMailApi(state())
    renderMailApp(`/mail?message=${html.ref}`)

    await userEvent.click(await screen.findByRole('button', { name: 'HTML' }))
    expect(screen.getByText('3 remote images are blocked, so the sender cannot tell you opened it.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Show remote images' }))
    await waitFor(() => {
      expect(screen.getByTitle(`HTML version of ${html.subject}`)).toHaveAttribute(
        'src',
        `/api/v1/mail/messages/${html.ref}/html?remote=1`,
      )
    })
    expect(screen.queryByRole('button', { name: 'Show remote images' })).not.toBeInTheDocument()
    expect(screen.getByText('2 remote images are shown for this message.')).toBeInTheDocument()

    // Text and back, in the same message: still revealed.
    await userEvent.click(screen.getByRole('button', { name: 'Text' }))
    await userEvent.click(screen.getByRole('button', { name: 'HTML' }))
    expect(await screen.findByTitle(`HTML version of ${html.subject}`)).toHaveAttribute(
      'src',
      `/api/v1/mail/messages/${html.ref}/html?remote=1`,
    )
    expect(screen.queryByRole('button', { name: 'Show remote images' })).not.toBeInTheDocument()

    // Another message, then back: remote images are blocked again.
    await userEvent.click(within(await messagesList()).getByRole('link', { name: /Message 99/ }))
    expect(await screen.findByText('Hello there.')).toBeInTheDocument()
    await userEvent.click(within(await messagesList()).getByRole('link', { name: /Message 98/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'HTML' }))
    expect(await screen.findByTitle(`HTML version of ${html.subject}`)).toHaveAttribute(
      'src',
      `/api/v1/mail/messages/${html.ref}/html`,
    )
    expect(screen.getByRole('button', { name: 'Show remote images' })).toBeInTheDocument()
  })

  it('offers no reveal when none of the blocked remote images can be shown', async () => {
    const fake = state()
    fake.views[html.ref] = messageView(html, { has_html: true, remote_images: 1, loadable_remote_images: 0 })
    stubMailApi(fake)
    renderMailApp(`/mail?message=${html.ref}`)

    await userEvent.click(await screen.findByRole('button', { name: 'HTML' }))
    expect(screen.getByText('1 remote image is blocked, so the sender cannot tell you opened it.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Show remote images' })).not.toBeInTheDocument()
  })

  it('offers to always show a sender only when DMARC passed for it', async () => {
    stubMailApi(state())
    renderMailApp(`/mail?message=${html.ref}`)

    await userEvent.click(await screen.findByRole('button', { name: 'HTML' }))
    expect(screen.getByRole('button', { name: 'Show remote images' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Always for this sender' })).not.toBeInTheDocument()
  })

  it('remembers a verified sender and then shows its remote images at once', async () => {
    const verified = { ...messageView(html).authentication, verified: true, dmarc: 'pass', dmarc_pass: true, from_domain: 'example.org' }
    const fetchMock = stubMailApi(
      state({
        views: {
          [html.ref]: messageView(html, { has_html: true, remote_images: 3, loadable_remote_images: 3, authentication: verified }),
          [plain.ref]: messageView(plain),
        },
      }),
    )
    renderMailApp(`/mail?message=${html.ref}`)

    await userEvent.click(await screen.findByRole('button', { name: 'HTML' }))
    await userEvent.click(screen.getByRole('button', { name: 'Always for this sender' }))
    await waitFor(() => {
      expect(screen.getByTitle(`HTML version of ${html.subject}`)).toHaveAttribute(
        'src',
        `/api/v1/mail/messages/${html.ref}/html?remote=1`,
      )
    })
    expect(bodiesTo(fetchMock, '/api/v1/mail/remote-senders')).toEqual([{ ref: html.ref }])
    expect(screen.getByText("3 remote images are shown: you always show this sender's.")).toBeInTheDocument()

    // Another message, then back: the server now says the sender is trusted.
    await userEvent.click(within(await messagesList()).getByRole('link', { name: /Message 99/ }))
    expect(await screen.findByText(`Body of ${plain.subject}`)).toBeInTheDocument()
    await userEvent.click(within(await messagesList()).getByRole('link', { name: /Message 98/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'HTML' }))
    expect(await screen.findByTitle(`HTML version of ${html.subject}`)).toHaveAttribute(
      'src',
      `/api/v1/mail/messages/${html.ref}/html?remote=1`,
    )
    expect(screen.queryByRole('button', { name: 'Show remote images' })).not.toBeInTheDocument()
  })

  it('loads remote images at once for a trusted sender', async () => {
    const fetchMock = stubMailApi(
      state({ views: { [htmlOnly.ref]: messageView(htmlOnly, { has_html: true, remote_images: 1, loadable_remote_images: 1, remote_trusted: true, text: { content: '', removed_chars: 0, incomplete: false, next_offset: null, quoted_omitted: false } }) } }),
    )
    renderMailApp(`/mail?message=${htmlOnly.ref}`)

    expect(await screen.findByTitle(`HTML version of ${htmlOnly.subject}`)).toHaveAttribute(
      'src',
      `/api/v1/mail/messages/${htmlOnly.ref}/html?remote=1`,
    )
    expect(queriesTo(fetchMock, `/api/v1/mail/messages/${htmlOnly.ref}/html`).map((query) => query.get('remote'))).toEqual(['1'])
    expect(screen.getByText("1 remote image is shown: you always show this sender's.")).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Show remote images' })).not.toBeInTheDocument()
  })

  it('says when the server would not remember the sender', async () => {
    const verified = { ...messageView(html).authentication, verified: true, dmarc: 'pass', dmarc_pass: true, from_domain: 'example.org' }
    stubMailApi(
      state({
        views: { [html.ref]: messageView(html, { has_html: true, remote_images: 2, loadable_remote_images: 2, authentication: verified }) },
        refuse: { 'POST /api/v1/mail/remote-senders': refusal(400, 'sender_not_verified') },
      }),
    )
    renderMailApp(`/mail?message=${html.ref}`)

    await userEvent.click(await screen.findByRole('button', { name: 'HTML' }))
    await userEvent.click(screen.getByRole('button', { name: 'Always for this sender' }))
    expect(await screen.findByText('This sender could not be verified, so it cannot be remembered.')).toHaveAttribute('role', 'alert')
    expect(screen.getByTitle(`HTML version of ${html.subject}`)).toHaveAttribute('src', `/api/v1/mail/messages/${html.ref}/html`)
  })

  it('opens a message with no text on its HTML, with no remote prompt when there is no remote content', async () => {
    stubMailApi(state())
    renderMailApp(`/mail?message=${htmlOnly.ref}`)

    const frame = await screen.findByTitle(`HTML version of ${htmlOnly.subject}`)
    expect(frame).toHaveAttribute('sandbox', HTML_SANDBOX)
    expect(screen.queryByRole('button', { name: 'Show remote images' })).not.toBeInTheDocument()
    expect(screen.queryByText(/remote image/)).not.toBeInTheDocument()
  })

  it('searches with the form fields and lists the results with their source', async () => {
    const fetchMock = stubMailApi(
      state({
        names: { [WORK]: { name: 'Work mail', colour: 'teal' }, [HOME]: { name: 'Home', colour: 'pink' } },
        searchResult: {
          messages: [messageItem(HOME, 'INBOX', 5, { subject: 'Invoice' })],
          next_cursor: null,
          failures: [
            { account_id: WORK, folder: null, code: 'auth_failed' },
            { account_id: HOME, folder: null, code: 'password_missing' },
          ],
        },
      }),
    )
    renderMailApp('/mail')

    await userEvent.type(await screen.findByRole('searchbox', { name: 'Search terms' }), 'invoice')
    await userEvent.click(screen.getByRole('button', { name: 'More options' }))
    await userEvent.selectOptions(screen.getByLabelText('Account'), 'All accounts')
    await userEvent.selectOptions(screen.getByLabelText('Folder'), 'All folders (not Junk or Trash)')
    await userEvent.type(screen.getByLabelText('From'), 'ada')
    await userEvent.click(screen.getByLabelText('Unread'))
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))

    const results = await screen.findByRole('list', { name: 'Search results' })
    expect(within(results).getByRole('link', { name: /Invoice/ })).toHaveTextContent(/Home·INBOX$/)
    const alert = within(await listRegion()).getByRole('alert')
    expect(alert).toHaveTextContent('Work mail: The mail server rejected the login for this account.')
    // The app's own code, worded as Accounts words it.
    expect(alert).toHaveTextContent('Home: No stored password works for this account')
    // The criteria go in the body; nothing is in the query string.
    expect(queriesTo(fetchMock, '/api/v1/mail/search').map(String)).toEqual([''])
    expect(bodiesTo(fetchMock, '/api/v1/mail/search')).toEqual([
      { account: '*', folder: '*', text: 'invoice', from: 'ada', unread: true },
    ])

    await userEvent.click(screen.getByRole('button', { name: 'Clear search' }))
    expect(await messagesList()).toBeInTheDocument()
  })

  it('words a folder that fails to list, and lets the reader retry', async () => {
    stubMailApi(state({ refuse: { 'GET /api/v1/mail/accounts/1/folders': refusal(409, 'auth_failed') } }))
    renderMailApp('/mail')

    const sidebar = await screen.findByRole('navigation', { name: 'Accounts and folders' })
    expect(await within(sidebar).findByRole('alert')).toHaveTextContent(
      'The mail server rejected the login for this account. Check its password in Accounts.',
    )
    expect(within(sidebar).getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('words an account that does not exist as such', async () => {
    stubMailApi(state())
    renderMailApp('/mail?account=gone')

    const [alert] = await within(await listRegion()).findAllByRole('alert')
    expect(alert).toHaveTextContent('This mail account does not exist (any more).')
  })

  it('words a folder that does not exist', async () => {
    stubMailApi(state())
    renderMailApp('/mail?folder=Gone')

    expect(await within(await listRegion()).findByRole('alert')).toHaveTextContent('That folder does not exist on this account.')
  })

  it('words a stale message reference, never the server text', async () => {
    stubMailApi(
      state({ refuse: { [`GET /api/v1/mail/messages/${plain.ref}`]: refusal(409, 'stale_reference') } }),
    )
    renderMailApp(`/mail?message=${plain.ref}`)

    const view = await screen.findByRole('region', { name: 'Message' })
    const alert = await within(view).findByRole('alert')
    expect(alert).toHaveTextContent('This message is out of date')
    expect(alert).not.toHaveTextContent('Server text')
  })

  it('lists the keyboard shortcuts on ? and from its button', async () => {
    stubMailApi(state())
    const history = renderMailApp('/mail')
    await messagesList()

    await userEvent.keyboard('?')
    const sheet = screen.getByRole('dialog', { name: 'Keyboard shortcuts' })
    expect(within(sheet).getByRole('heading', { name: 'Acting on messages' })).toBeInTheDocument()
    expect(within(sheet).getByText('Move to Trash').previousSibling).toHaveTextContent('#')
    expect(within(sheet).getByText('Star or unstar').previousSibling).toHaveTextContent('s')
    expect(within(sheet).getByText('Next message').previousSibling).toHaveTextContent('j')
    expect(within(sheet).getByText('Reply all').previousSibling).toHaveTextContent('a')
    // The sheet takes the keys: `j` opens no message behind it.
    await userEvent.keyboard('j')
    expect(history.location.search).not.toContain('message')
    // What a browser's dialog fires on Escape, which jsdom's does not.
    fireEvent(sheet, new Event('cancel', { cancelable: true }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Keyboard shortcuts' }))
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Keyboard shortcuts' })).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('points to Accounts when there is no account', async () => {
    stubMailApi(state({ accounts: [] }))
    renderMailApp('/mail')

    await userEvent.click(await screen.findByRole('link', { name: 'Go to Accounts' }))
    expect(await screen.findByText('Accounts page')).toBeInTheDocument()
  })
})

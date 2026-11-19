import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { bodiesTo, type FakeMailState, HOME, messageItem, messageView, queriesTo, refusal, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { onSessionEnded } from '@/api/http.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

import { HTML_SANDBOX } from './html-body.tsx'

const inbox = Array.from({ length: 30 }, (_, index) =>
  messageItem(WORK, 'INBOX', 100 - index, index === 0 ? { flags: { seen: false, flagged: true, answered: false, draft: false, to_sort: false } } : {}),
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
    expect(screen.getByRole('link', { name: 'home' })).toBeInTheDocument()

    const list = await messagesList()
    expect(within(list).getAllByRole('listitem')).toHaveLength(25)
    expect(within(list).getByRole('link', { name: /Unread:\s*Message 100/ })).toBeInTheDocument()
    expect(within(list).getByRole('button', { name: 'Star', pressed: true })).toBeInTheDocument()
  })

  it('names each account by its name, with its colour, never by its id', async () => {
    stubMailApi(state({ names: { [WORK]: { name: 'Work mail', colour: 'teal' }, [HOME]: { name: 'Home', colour: null } } }))
    renderMailApp('/mail')

    expect(await screen.findByRole('tree', { name: 'Folders of Work mail' })).toBeInTheDocument()
    const sidebar = screen.getByRole('navigation', { name: 'Accounts and folders' })
    expect(within(sidebar).getByRole('heading', { name: 'Work mail' })).toBeInTheDocument()
    expect(within(sidebar).getByRole('button', { name: 'New folder in Work mail' })).toBeInTheDocument()
    expect(within(sidebar).getByRole('link', { name: 'Home' })).toBeInTheDocument()
    expect(within(sidebar).queryByText('work')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'More options' }))
    const accounts = within(screen.getByLabelText('Account')).getAllByRole('option')
    expect(accounts.map((option) => [option.getAttribute('value'), option.textContent])).toEqual([
      ['*', 'All accounts'],
      ['1', 'Work mail'],
      ['2', 'Home'],
    ])
  })

  it('loads the next page with the cursor the last one gave, until there is none', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
    expect(await within(list).findByRole('link', { name: /Message 71/ })).toBeInTheDocument()
    expect(within(list).getAllByRole('listitem')).toHaveLength(30)
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
    const cursors = queriesTo(fetchMock, '/api/v1/mail/accounts/1/messages').map((query) => query.get('cursor'))
    expect(cursors).toEqual([null, 'before:76'])
  })

  it('words a page that fails to load more, and keeps what was shown', async () => {
    const fake = state()
    stubMailApi(fake)
    renderMailApp('/mail')

    const list = await messagesList()
    fake.refuse = { 'GET /api/v1/mail/accounts/1/messages': refusal(504, 'timeout') }
    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
    expect(await within(await listRegion()).findByRole('alert')).toHaveTextContent('The mail server did not answer in time.')
    expect(within(list).getAllByRole('listitem')).toHaveLength(25)
    expect(screen.getByRole('button', { name: 'Load more' })).toBeInTheDocument()
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

    await userEvent.click(screen.getByRole('link', { name: 'home' }))
    expect(await screen.findByRole('link', { name: /From home/ })).toBeInTheDocument()
    expect(await screen.findByRole('tree', { name: 'Folders of home' })).toBeInTheDocument()
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
    const include = within(screen.getByRole('menu')).getByRole('menuitemcheckbox', { name: 'Include subfolders' })
    expect(include).toHaveAttribute('aria-checked', 'false')
    await userEvent.click(include)

    const list = await screen.findByRole('list', { name: 'Messages' })
    expect(await within(list).findByRole('link', { name: /Release notes/ })).toHaveTextContent('INBOX/Lists')
    expect(within(list).getByRole('link', { name: /Hello/ })).toHaveTextContent(/INBOX$/)
    expect(history.location.search).toContain('sub=true')
    expect(queriesTo(fetchMock, '/api/v1/mail/accounts/1/messages').map((query) => query.get('subfolders'))).toEqual([null, 'true'])
    // Its count is what the list reads: Trash under it is left out.
    expect(within(tree).getByRole('treeitem', { name: 'Inbox, with subfolders, 3 unread' })).toBeInTheDocument()

    // A search in the folder reads its subfolders too; one in every folder does not.
    await userEvent.type(screen.getByRole('searchbox', { name: 'Words' }), 'notes')
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
    const checked = within(screen.getByRole('menu')).getByRole('menuitemcheckbox', { name: 'Include subfolders' })
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
    stubMailApi(state())
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

  it('shows HTML only in an iframe of the HTML document, sandboxed without scripts or same origin', async () => {
    stubMailApi(state())
    renderMailApp(`/mail?message=${html.ref}`)

    // The text is the default.
    expect(await screen.findByText(`Body of ${html.subject}`)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'HTML' }))
    const frame = await screen.findByTitle(`HTML version of ${html.subject}`)
    expect(frame.tagName).toBe('IFRAME')
    expect(frame.getAttribute('sandbox')).toBe('allow-popups allow-popups-to-escape-sandbox')
    expect(HTML_SANDBOX).not.toMatch(/allow-scripts|allow-same-origin/)
    expect(frame).toHaveAttribute('referrerpolicy', 'no-referrer')
    expect(frame).toHaveAttribute('src', `/api/v1/mail/messages/${html.ref}/html`)
    expect(frame).not.toHaveAttribute('srcdoc')
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

  it('opens a message with no text on its HTML, with no remote prompt when there is no remote content', async () => {
    stubMailApi(state())
    renderMailApp(`/mail?message=${htmlOnly.ref}`)

    const frame = await screen.findByTitle(`HTML version of ${htmlOnly.subject}`)
    expect(frame).toHaveAttribute('sandbox', HTML_SANDBOX)
    expect(screen.queryByRole('button', { name: 'Show remote images' })).not.toBeInTheDocument()
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

    await userEvent.type(await screen.findByRole('searchbox', { name: 'Words' }), 'invoice')
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

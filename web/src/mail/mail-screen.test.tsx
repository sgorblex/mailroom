import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  bodiesTo,
  messageItem,
  messageView,
  queriesTo,
  refusal,
  stubMailApi,
  type FakeMailState,
} from '@/test/fake-mail-api.ts'
import { onSessionEnded } from '@/api/http.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

import { HTML_SANDBOX } from './html-body.tsx'

const inbox = Array.from({ length: 30 }, (_, index) =>
  messageItem('work', 'INBOX', 100 - index, index === 0 ? { flags: { seen: false, flagged: true, answered: false, draft: false } } : {}),
)
const plain = inbox[1] ?? messageItem('work', 'INBOX', 0)
const html = inbox[2] ?? messageItem('work', 'INBOX', 0)
const htmlOnly = inbox[3] ?? messageItem('work', 'INBOX', 0)
const long = inbox[4] ?? messageItem('work', 'INBOX', 0)

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: ['work', 'home'],
    folders: {
      work: [
        { path: 'INBOX', role: 'inbox', unread: 3, total: 30 },
        { path: 'Sent Messages', role: 'sent', unread: 0, total: 4 },
        { path: 'Archive/2025', role: null, unread: 0, total: 2 },
      ],
      home: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 1 }],
    },
    messages: {
      'work/INBOX': inbox,
      'work/Archive/2025': [messageItem('work', 'Archive/2025', 7, { subject: 'Old news' })],
      'home/INBOX': [messageItem('home', 'INBOX', 1, { subject: 'From home' })],
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
      [html.ref]: messageView(html, { has_html: true, has_remote_content: true }),
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

afterEach(() => {
  vi.unstubAllGlobals()
  onSessionEnded(null)
})

describe('MailScreen', () => {
  it("opens the first account's inbox, with folder roles and unread counts", async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const folders = await screen.findByRole('list', { name: 'Folders of work' })
    const inboxLink = within(folders).getByRole('link', { name: 'Inbox, 3 unread' })
    expect(inboxLink).toHaveAttribute('aria-current', 'page')
    expect(within(folders).getByRole('link', { name: 'Sent' })).toHaveAttribute('title', 'Sent Messages')
    expect(within(folders).getByRole('link', { name: 'Archive/2025' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'home' })).toBeInTheDocument()

    const list = await messagesList()
    expect(within(list).getAllByRole('listitem')).toHaveLength(25)
    expect(within(list).getByRole('link', { name: /Unread:\s*Message 100/ })).toBeInTheDocument()
    expect(within(list).getByLabelText('Starred')).toBeInTheDocument()
  })

  it('loads the next page with the cursor the last one gave, until there is none', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
    expect(await within(list).findByRole('link', { name: /Message 71/ })).toBeInTheDocument()
    expect(within(list).getAllByRole('listitem')).toHaveLength(30)
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
    const cursors = queriesTo(fetchMock, '/api/v1/mail/accounts/work/messages').map((query) => query.get('cursor'))
    expect(cursors).toEqual([null, 'at:25'])
  })

  it('words a page that fails to load more, and keeps what was shown', async () => {
    const fake = state()
    stubMailApi(fake)
    renderMailApp('/mail')

    const list = await messagesList()
    fake.refuse = { 'GET /api/v1/mail/accounts/work/messages': refusal(504, 'timeout') }
    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The mail server did not answer in time.')
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

    await userEvent.click(await screen.findByRole('link', { name: 'Archive/2025' }))
    expect(await screen.findByRole('link', { name: /Old news/ })).toBeInTheDocument()
    expect(history.location.search).toContain('folder=Archive%2F2025')

    await userEvent.click(screen.getByRole('link', { name: 'home' }))
    expect(await screen.findByRole('link', { name: /From home/ })).toBeInTheDocument()
    expect(await screen.findByRole('list', { name: 'Folders of home' })).toBeInTheDocument()
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
    expect(screen.getByText('Remote images are blocked, so the sender cannot tell you opened it.')).toBeInTheDocument()
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
        searchResult: {
          messages: [messageItem('home', 'INBOX', 5, { subject: 'Invoice' })],
          next_cursor: null,
          failures: [
            { account_id: 'work', folder: null, code: 'auth_failed' },
            { account_id: 'home', folder: null, code: 'password_missing' },
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
    expect(within(results).getByText('home · INBOX')).toBeInTheDocument()
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('work: The mail server rejected the login for this account.')
    // The app's own code, worded as Accounts words it.
    expect(alert).toHaveTextContent('home: No stored password works for this account')
    // The criteria go in the body; nothing is in the query string.
    expect(queriesTo(fetchMock, '/api/v1/mail/search').map(String)).toEqual([''])
    expect(bodiesTo(fetchMock, '/api/v1/mail/search')).toEqual([
      { account: '*', folder: '*', text: 'invoice', from: 'ada', unread: true },
    ])

    await userEvent.click(screen.getByRole('button', { name: 'Clear search' }))
    expect(await messagesList()).toBeInTheDocument()
  })

  it('words a folder that fails to list, and lets the reader retry', async () => {
    stubMailApi(state({ refuse: { 'GET /api/v1/mail/accounts/work/folders': refusal(409, 'auth_failed') } }))
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

    const [alert] = await screen.findAllByRole('alert')
    expect(alert).toHaveTextContent('This mail account does not exist (any more).')
  })

  it('words a folder that does not exist', async () => {
    stubMailApi(state())
    renderMailApp('/mail?folder=Gone')

    expect(await screen.findByRole('alert')).toHaveTextContent('That folder does not exist on this account.')
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

  it('points to Accounts when there is no account', async () => {
    stubMailApi(state({ accounts: [] }))
    renderMailApp('/mail')

    await userEvent.click(await screen.findByRole('link', { name: 'Go to Accounts' }))
    expect(await screen.findByText('Accounts page')).toBeInTheDocument()
  })
})

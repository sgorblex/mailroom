import { vi, type Mock } from 'vitest'

import type { MailFolder, MessageItem, MessagePage, MessageView } from '@/api/mail.ts'

export interface FakeMailState {
  accounts: string[]
  /** Each account's folders. */
  folders: Record<string, MailFolder[]>
  /** `"<account>/<folder path>"` → its messages, newest first. */
  messages: Record<string, MessageItem[]>
  /** Message views by ref; `include_quoted=true` answers `quoted` instead when it has one. */
  views: Record<string, MessageView & { quoted?: MessageView['text'] }>
  /** What a search answers, whatever the query. */
  searchResult?: MessagePage
  /** Requests (`"GET <path without query>"`, `"POST /api/v1/mail/search"`) answered with this status and body instead. */
  refuse?: Record<string, { status: number; body: unknown }>
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

/** A coded mail refusal, as the API words one (`docs/requirements/mail-client.md`). */
export function refusal(status: number, code: string): { status: number; body: unknown } {
  return { status, body: { detail: { code, message: `Server text for ${code}` } } }
}

const notFound = (): Response => json(404, { detail: { code: 'not_found', message: 'No such message or part.' } })

/** A list item; `ref` is made up from the account, folder and uid, opaque as the real one. */
export function messageItem(account: string, folder: string, uid: number, fields: Partial<MessageItem> = {}): MessageItem {
  return {
    ref: `ref-${account}-${folder}-${String(uid)}`,
    account_id: account,
    folder,
    date: '2026-10-30T09:12:00+00:00',
    from: { name: `Sender ${String(uid)}`, address: `sender${String(uid)}@example.org` },
    subject: `Message ${String(uid)}`,
    flags: { seen: true, flagged: false, answered: false, draft: false },
    has_attachments: false,
    preview: `Preview of ${String(uid)}`,
    size: 1000,
    ...fields,
  }
}

/** A message view of `item`, plain text unless `fields` say otherwise. */
export function messageView(item: MessageItem, fields: Partial<MessageView> = {}): MessageView {
  return {
    ref: item.ref,
    account_id: item.account_id,
    folder: item.folder,
    from: item.from,
    to: [{ name: '', address: 'me@example.org' }],
    cc: [],
    reply_to: [],
    date: item.date,
    subject: item.subject,
    message_id: '<id@example.org>',
    flags: item.flags,
    text: {
      content: `Body of ${item.subject}`,
      removed_chars: 0,
      incomplete: false,
      next_offset: null,
      quoted_omitted: false,
    },
    has_html: false,
    has_remote_content: false,
    attachments: [],
    calendar: null,
    ...fields,
  }
}

/**
 * A fake mail API over `state`: `/api/v1/accounts` and the `/api/v1/mail` routes. Cursors are
 * `"at:<index>"`, as opaque to the client as the real ones.
 */
export function stubMailApi(state: FakeMailState): Mock {
  const respond = (url: URL): Response => {
    const path = url.pathname
    const query = url.searchParams
    if (path === '/api/v1/accounts') {
      return json(
        200,
        state.accounts.map((id) => {
          const endpoint = { host: `imap.${id}.example`, port: 993, tls: 'implicit' }
          return {
            id,
            login: `me@${id}.example`,
            send_mode: 'drafts',
            imap: endpoint,
            smtp: endpoint,
            identities: [],
            auto_approve: [],
            has_password: true,
            run_filters: false,
          }
        }),
      )
    }
    let match = /^\/api\/v1\/mail\/accounts\/([^/]+)\/(folders|messages)$/.exec(path)
    if (match !== null) {
      const account = decodeURIComponent(match[1] ?? '')
      if (!state.accounts.includes(account)) {
        return notFound()
      }
      if (match[2] === 'folders') {
        return json(200, { account_id: account, folders: state.folders[account] ?? [] })
      }
      const folder = query.get('folder') ?? 'INBOX'
      const all = state.messages[`${account}/${folder}`]
      if (all === undefined) {
        return json(404, { detail: { code: 'mailbox_missing', message: 'That folder does not exist.' } })
      }
      const limit = Number(query.get('limit') ?? '25')
      const start = Number((query.get('cursor') ?? 'at:0').slice(3))
      const end = start + limit
      return json(200, {
        messages: all.slice(start, end),
        next_cursor: end < all.length ? `at:${String(end)}` : null,
        failures: [],
      })
    }
    if (path === '/api/v1/mail/search') {
      return json(200, state.searchResult ?? { messages: [], next_cursor: null, failures: [] })
    }
    match = /^\/api\/v1\/mail\/messages\/([^/]+)\/html$/.exec(path)
    if (match !== null) {
      const view = state.views[decodeURIComponent(match[1] ?? '')]
      if (view?.has_html !== true) {
        return notFound()
      }
      return new Response('<!doctype html><p>HTML</p>', { status: 200, headers: { 'Content-Type': 'text/html' } })
    }
    match = /^\/api\/v1\/mail\/messages\/([^/]+)$/.exec(path)
    if (match !== null) {
      const view = state.views[decodeURIComponent(match[1] ?? '')]
      if (view === undefined) {
        return notFound()
      }
      const { quoted, ...rest } = view
      const text = query.get('include_quoted') === 'true' && quoted !== undefined ? quoted : rest.text
      const offset = Number(query.get('offset') ?? '0')
      return json(200, { ...rest, text: offset === 0 ? text : { ...text, content: text.content.slice(offset) } })
    }
    return json(404, { detail: 'Not Found' })
  }
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    const url = new URL(path, 'http://mailroom.test')
    const refused = state.refuse?.[`${init?.method ?? 'GET'} ${url.pathname}`]
    if (refused !== undefined) {
      return Promise.resolve(json(refused.status, refused.body))
    }
    return Promise.resolve(respond(url))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** The JSON bodies of the POST requests to `path`, in order. */
export function bodiesTo(fetchMock: Mock, path: string): unknown[] {
  return fetchMock.mock.calls
    .filter(([callPath]) => new URL(callPath as string, 'http://mailroom.test').pathname === path)
    .map(([, init]) => {
      const body = (init as RequestInit | undefined)?.body
      return typeof body === 'string' ? (JSON.parse(body) as unknown) : null
    })
}

/** The query strings of the GET requests to `path`, in order. */
export function queriesTo(fetchMock: Mock, path: string): URLSearchParams[] {
  return fetchMock.mock.calls
    .map(([callPath]) => new URL(callPath as string, 'http://mailroom.test'))
    .filter((url) => url.pathname === path)
    .map((url) => url.searchParams)
}

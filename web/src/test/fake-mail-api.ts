import { vi, type Mock } from 'vitest'

import type { MailFolder, MessageItem, MessagePage, MessageView, OrganizeGroup } from '@/api/mail.ts'

export interface FakeMailState {
  accounts: string[]
  /** Each account's folders. */
  folders: Record<string, MailFolder[]>
  /** `"<account>/<folder path>"` → its messages, newest (highest uid) first. */
  messages: Record<string, MessageItem[]>
  /** Message views by ref; `include_quoted=true` answers `quoted` instead when it has one. */
  views: Record<string, MessageView & { quoted?: MessageView['text'] }>
  /** What a search answers, whatever the query. */
  searchResult?: MessagePage
  /** Requests (`"GET <path without query>"`, `"POST /api/v1/mail/search"`) answered with this status and body instead. */
  refuse?: Record<string, { status: number; body: unknown }>
  /** `"<account>/<folder path>"` → the code its group fails with in an organize batch. */
  groupFailures?: Record<string, string>
  /** Called with each request (`"POST /api/v1/mail/messages/flag"`, its body); the answer waits for the promise it returns. */
  hold?: (request: string, body: unknown) => Promise<void> | undefined
}

/** The statuses of the mail codes the organize routes answer (`docs/requirements/mail-client.md`). */
const STATUS: Record<string, number> = {
  invalid_argument: 400,
  not_found: 404,
  mailbox_missing: 404,
  stale_reference: 409,
  auth_failed: 409,
  busy: 429,
  timeout: 504,
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

/** A coded mail refusal, as the API words one (`docs/requirements/mail-client.md`). */
export function refusal(status: number, code: string): { status: number; body: unknown } {
  return { status, body: { detail: { code, message: `Server text for ${code}` } } }
}

const notFound = (): Response => json(404, { detail: { code: 'not_found', message: 'No such message or part.' } })

/** A ref made up from the account, folder and uid, opaque to the client as the real one. */
function fakeRef(account: string, folder: string, uid: number): string {
  return `ref-${account}-${folder}-${String(uid)}`
}

/** The uid `fakeRef` put in the ref of `item`. */
function uidOf(item: MessageItem): number {
  return Number(/-(\d+)$/.exec(item.ref)?.[1] ?? Number.NaN)
}

/** A list item; `ref` is made up from the account, folder and uid (`fakeRef`). */
export function messageItem(account: string, folder: string, uid: number, fields: Partial<MessageItem> = {}): MessageItem {
  return {
    ref: fakeRef(account, folder, uid),
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
 * The organize routes over `state`: a move takes the messages out of their folder's list into the
 * destination's (a new ref, as after COPYUID), a flag change writes the list item and the view.
 */
function organize(state: FakeMailState, route: string, body: { refs: string[]; folder?: string; seen?: boolean; flagged?: boolean }): Response {
  if (body.refs.length === 0 || body.refs.length > 50) {
    return json(422, { detail: [{ type: 'too_long', loc: ['body', 'refs'], msg: 'List should have at most 50 items' }] })
  }
  const located = body.refs.map((ref) => {
    for (const [key, items] of Object.entries(state.messages)) {
      const item = items.find((message) => message.ref === ref)
      if (item !== undefined) {
        return { key, item }
      }
    }
    return null
  })
  if (located.some((found) => found === null)) {
    return notFound()
  }
  const groups = new Map<string, MessageItem[]>()
  for (const found of located) {
    if (found !== null) {
      groups.set(found.key, [...(groups.get(found.key) ?? []), found.item])
    }
  }
  const results: OrganizeGroup[] = [...groups].map(([key, items]) => {
    const { account_id: account, folder } = items[0] ?? messageItem('', '', 0)
    const group = { account, folder, ok: true, count: items.length, new_refs: null, junk: null, flags: [], error: null }
    const failed = (code: string): OrganizeGroup => ({
      ...group,
      ok: false,
      count: 0,
      error: { code, message: `Server text for ${code}` },
    })
    const code = state.groupFailures?.[key]
    if (code !== undefined) {
      return failed(code)
    }
    if (route === 'flag') {
      // Written anew, never in place: tests share their items.
      const changed = new Map(
        items.map((item) => [
          item.ref,
          { ...item.flags, seen: body.seen ?? item.flags.seen, flagged: body.flagged ?? item.flags.flagged },
        ]),
      )
      state.messages[key] = (state.messages[key] ?? []).map((item) => {
        const flags = changed.get(item.ref)
        return flags === undefined ? item : { ...item, flags }
      })
      for (const [ref, flags] of changed) {
        const view = state.views[ref]
        if (view !== undefined) {
          state.views[ref] = { ...view, flags }
        }
      }
      return { ...group, new_refs: [], flags: [...changed].map(([ref, flags]) => ({ ref, seen: flags.seen, flagged: flags.flagged })) }
    }
    const role = route === 'archive' ? 'archive' : route === 'trash' ? 'trash' : null
    const destination = role === null ? body.folder : state.folders[account]?.find((each) => each.role === role)?.path
    if (destination === undefined) {
      return failed('unsupported')
    }
    // New uids above the destination's, as a server gives them, so its list stays in uid order.
    const there = state.messages[`${account}/${destination}`] ?? []
    const top = Math.max(0, ...there.map(uidOf))
    const moved = items.map((item, index) => ({
      ...item,
      ref: fakeRef(account, destination, top + items.length - index),
      folder: destination,
    }))
    state.messages[key] = (state.messages[key] ?? []).filter((item) => !items.includes(item))
    state.messages[`${account}/${destination}`] = [...moved, ...there]
    return { ...group, new_refs: moved.map((item) => item.ref) }
  })
  const first = results.find((result) => result.error !== null)
  if (first?.error != null && results.every((result) => !result.ok)) {
    return json(STATUS[first.error.code] ?? 502, { detail: first.error })
  }
  return json(200, { results })
}

/**
 * A fake mail API over `state`: `/api/v1/accounts` and the `/api/v1/mail` routes. A cursor is
 * `"before:<uid>"`, the uid of the last item shown, as opaque to the client as the real ones and,
 * like mailroom-core's, it goes on below that uid whether or not that message is still there.
 */
export function stubMailApi(state: FakeMailState): Mock {
  const respond = (url: URL, init?: RequestInit): Response => {
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
      const cursor = query.get('cursor')
      const before = cursor === null ? Number.POSITIVE_INFINITY : Number(cursor.slice('before:'.length))
      const rest = all.filter((item) => uidOf(item) < before)
      const shown = rest.slice(0, limit)
      const last = shown[shown.length - 1]
      return json(200, {
        messages: shown,
        next_cursor: rest.length > limit && last !== undefined ? `before:${String(uidOf(last))}` : null,
        failures: [],
      })
    }
    match = /^\/api\/v1\/mail\/messages\/(move|archive|trash|flag)$/.exec(path)
    if (match !== null && init?.method === 'POST' && typeof init.body === 'string') {
      return organize(state, match[1] ?? '', JSON.parse(init.body) as Parameters<typeof organize>[2])
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
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const url = new URL(path, 'http://mailroom.test')
    const request = `${init?.method ?? 'GET'} ${url.pathname}`
    const held = state.hold?.(request, typeof init?.body === 'string' ? JSON.parse(init.body) : null)
    if (held !== undefined) {
      await held
    }
    const refused = state.refuse?.[request]
    if (refused !== undefined) {
      return json(refused.status, refused.body)
    }
    return respond(url, init)
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

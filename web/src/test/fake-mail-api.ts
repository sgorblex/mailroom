import { vi, type Mock } from 'vitest'

import type { Identity } from '@/api/accounts.ts'
import type {
  Draft,
  DraftFields,
  MailFolder,
  MessageItem,
  MessagePage,
  MessagePart,
  MessageView,
  OrganizeGroup,
  SendResult,
} from '@/api/mail.ts'

export type FakeFolder = Omit<MailFolder, 'delimiter' | 'selectable'> & Partial<Pick<MailFolder, 'delimiter' | 'selectable'>>

/** A fake folder as the API lists it. */
export function listedFolder(folder: FakeFolder): MailFolder {
  return { delimiter: '/', selectable: true, ...folder }
}

export interface FakeMailState {
  accounts: string[]
  /** Each account's folders; `delimiter` is `/` and `selectable` true unless they say otherwise. */
  folders: Record<string, FakeFolder[]>
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
  /** `"<account>/<folder path>"` → the code its group fails with after copying, the messages in both. */
  copiedNotRemoved?: Record<string, string>
  /** Called with each request (`"POST /api/v1/mail/messages/flag"`, its body); the answer waits for the promise it returns. */
  hold?: (request: string, body: unknown) => Promise<void> | undefined
  /** Each account's send mode; `drafts` unless it says otherwise. */
  sendModes?: Record<string, string>
  identities?: Record<string, Identity[]>
  attachmentMaxMb?: number
  /** The user's limit on To, Cc and Bcc together; 10 unless it says otherwise. */
  maxRecipients?: number
  /** Drafts as `GET /drafts/{ref}` reads them, newest last. */
  drafts?: Draft[]
  /** `false`: the server lacks UIDPLUS. */
  autosave?: boolean
  /**
   * The next save is answered with no ref, as when neither APPENDUID nor a search found it: the
   * version it was to replace stays, and the new one joins `unindexed`.
   */
  saveWithoutRef?: boolean
  /** Refs a search does not find yet, though the drafts are there. */
  unindexed?: string[]
  /** The next save cannot remove the version it replaces: both stay, and it answers `replaced: false`. */
  keepReplaced?: boolean
  /** What the next sends end as; a plain `sent` without it. */
  sendOutcome?: Pick<SendResult, 'state'> & Partial<SendResult>
  /** How many polls a send answers `sending` before it ends. */
  sendingPolls?: number
  /** How many polls a `sent` send answers unsettled, as a real one does until filing and what follows are done. */
  filingPolls?: number
  /** Claimed sends by key, as `core.sends` holds them. */
  sends?: Record<string, SendResult & { polls: number; filingPolls?: number }>
  /** What reached SMTP, once per delivery. */
  delivered?: { key: string; ref: string; text: string }[]
  /** The request is carried out, and its answer never comes back. */
  lose?: (request: string) => boolean
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
function organize(
  state: FakeMailState,
  route: string,
  body: { refs: string[]; folder?: string; account?: string; removal?: string; seen?: boolean; flagged?: boolean },
): Response {
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
    const group: OrganizeGroup = {
      account,
      folder,
      ok: true,
      count: items.length,
      new_refs: null,
      destination_account: null,
      junk: null,
      flags: [],
      error: null,
    }
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
    // A move to another account lands there; any other move stays in the account.
    const to = route === 'move-to-account' ? (body.account ?? account) : account
    // New uids above the destination's, as a server gives them, so its list stays in uid order.
    const there = state.messages[`${to}/${destination}`] ?? []
    const top = Math.max(0, ...there.map(uidOf))
    const moved = items.map((item, index) => ({
      ...item,
      ref: fakeRef(to, destination, top + items.length - index),
      account_id: to,
      folder: destination,
    }))
    const newRefs = moved.map((item) => item.ref)
    const copiedOnly = state.copiedNotRemoved?.[key]
    state.messages[`${to}/${destination}`] = [...moved, ...there]
    if (copiedOnly !== undefined) {
      // Copied there, but still here: the group fails with the copies' refs.
      return { ...failed(copiedOnly), destination_account: to, new_refs: newRefs }
    }
    state.messages[key] = (state.messages[key] ?? []).filter((item) => !items.includes(item))
    return { ...group, new_refs: newRefs, destination_account: route === 'move-to-account' ? to : null }
  })
  const first = results.find((result) => result.error !== null)
  // A move to another account that made copies answers them, failed or not: they are in both.
  const copied = route === 'move-to-account' && results.some((result) => (result.new_refs?.length ?? 0) > 0)
  if (first?.error != null && results.every((result) => !result.ok) && !copied) {
    return json(STATUS[first.error.code] ?? 502, { detail: first.error })
  }
  return json(200, { results })
}

const refuse = (status: number, code: string): Response => {
  const { body } = refusal(status, code)
  return json(status, body)
}

/** A save refused for the send that holds its draft, which the refusal names. */
function heldRefusal(send: { send_id: string; state: string; code: string | null }): { status: number; body: unknown } {
  const { send_id, state, code } = send
  return {
    status: 409,
    body: {
      detail: { code: 'draft_not_editable', message: 'Server text for draft_not_editable', send: { send_id, state, code } },
    },
  }
}

let nextUid = 500

function defaultIdentity(state: FakeMailState, account: string): string | null {
  const identities = state.identities?.[account] ?? []
  return (identities.find((each) => each.default === true) ?? identities[0])?.address ?? null
}

function isOff(state: FakeMailState, account: string): boolean {
  return state.sendModes?.[account] === 'off'
}

function deliverable(address: string): boolean {
  return /^[!-~]+@[!-~]+$/.test(address)
}

/** The account's Drafts list holds the draft under its new ref, as the server's folder would. */
function listDraft(state: FakeMailState, draft: Draft, previous: string | null): void {
  const key = `${draft.account}/Drafts`
  const list = state.messages[key]
  if (list === undefined) {
    return
  }
  const uid = Number(/-(\d+)$/.exec(draft.ref)?.[1] ?? 0)
  const item = messageItem(draft.account, 'Drafts', uid, { ref: draft.ref, subject: draft.subject })
  state.messages[key] = [item, ...list.filter((each) => each.ref !== previous)]
  state.views[draft.ref] = messageView(item, { flags: { ...item.flags, draft: true } })
}

function unlistDraft(state: FakeMailState, draft: Draft): void {
  const key = `${draft.account}/Drafts`
  const list = state.messages[key]
  if (list !== undefined) {
    state.messages[key] = list.filter((each) => each.ref !== draft.ref)
  }
}

/** The attachment a `keep` entry names: a part of another draft or of a message view. */
function keptPart(state: FakeMailState, keep: { ref: string; part: string }): MessagePart | undefined {
  const parts = state.drafts?.find((each) => each.ref === keep.ref)?.parts ?? state.views[keep.ref]?.attachments
  return parts?.find((part) => part.part === keep.part)
}

function holdsDraft(send: { state: string } | null | undefined): boolean {
  return send !== null && send !== undefined && send.state !== 'failed'
}

function saveDraftAnswer(state: FakeMailState, form: FormData): Response {
  const fields = JSON.parse(form.get('draft') as string) as DraftFields
  if (isOff(state, fields.account)) {
    return refuse(409, 'send_disabled')
  }
  const addresses = [...fields.to, ...fields.cc, ...fields.bcc]
  if (addresses.some((each) => !deliverable(each.address))) {
    return refuse(422, 'recipient_refused')
  }
  if (addresses.length > (state.maxRecipients ?? 10)) {
    return refuse(400, 'invalid_argument')
  }
  const drafts = state.drafts ?? []
  const old = fields.ref === null ? undefined : drafts.find((each) => each.ref === fields.ref)
  const own = fields.ref === null || (old !== undefined && old.compose_id !== null)
  const holding = own ? null : (state.sends?.[fields.ref ?? ''] ?? old?.send)
  if (holding !== null && holding !== undefined && holdsDraft(holding)) {
    const { status, body } = heldRefusal(holding)
    return json(status, body)
  }
  if (fields.ref !== null && old === undefined) {
    return refuse(404, 'not_found')
  }
  const kept = fields.keep.map((keep) => keptPart(state, keep))
  if (kept.includes(undefined)) {
    return refuse(404, 'not_found')
  }
  const files = form.getAll('files[]') as File[]
  const parts = [...kept.filter((part) => part !== undefined), ...files.map((file) => ({ name: file.name, type: file.type, size: file.size, part: '' }))].map(
    (part, index) => ({ ...part, part: String(index + 2) }),
  )
  nextUid += 1
  const ref = fakeRef(fields.account, 'Drafts', nextUid)
  const draft: Draft = {
    ref,
    account: fields.account,
    compose_id: fields.compose_id,
    from: fields.from,
    to: fields.to,
    cc: fields.cc,
    bcc: fields.bcc,
    subject: fields.subject,
    text: fields.text,
    answers: fields.answers,
    lossy: false,
    parts,
    send: null,
  }
  const autosave = state.autosave ?? true
  const hidden = state.saveWithoutRef === true
  const oldStays = hidden || state.keepReplaced === true
  state.saveWithoutRef = false
  state.keepReplaced = false
  state.drafts = [...drafts.filter((each) => oldStays || each !== old), draft]
  listDraft(state, draft, oldStays ? null : fields.ref)
  if (hidden) {
    state.unindexed = [...(state.unindexed ?? []), ref]
    return json(201, { ref: null, parts: [], replaced: false, autosave })
  }
  return json(201, { ref, parts, replaced: old !== undefined && !oldStays, autosave })
}

function prefillAnswer(state: FakeMailState, body: { ref: string; mode: string }): Response {
  const view = state.views[body.ref]
  if (view === undefined) {
    return notFound()
  }
  if (isOff(state, view.account_id)) {
    return refuse(409, 'send_disabled')
  }
  const forward = body.mode === 'forward'
  const sender = view.reply_to[0] ?? view.from
  return json(200, {
    account: view.account_id,
    from: defaultIdentity(state, view.account_id),
    to: forward || sender === null ? [] : [sender],
    cc: body.mode === 'reply_all' ? view.to : [],
    bcc: [],
    subject: `${forward ? 'Fwd' : 'Re'}: ${view.subject}`,
    text: `\n\n> ${view.text.content}`,
    answers: forward ? null : view.ref,
    attachments: forward ? view.attachments.map((part) => ({ ...part, ref: view.ref })) : [],
  })
}

function sendAnswer(state: FakeMailState, draft: Draft, confirmResend: boolean): Response {
  if (isOff(state, draft.account)) {
    return refuse(409, 'send_disabled')
  }
  if (draft.to.length + draft.cc.length + draft.bcc.length === 0) {
    return refuse(400, 'invalid_argument')
  }
  const key = draft.compose_id ?? draft.ref
  state.sends ??= {}
  const existing = state.sends[key]
  if (existing !== undefined) {
    if (existing.state === 'sent') {
      return json(200, existing)
    }
    if (existing.state === 'sending') {
      return json(202, { send_id: existing.send_id })
    }
    if (existing.state === 'unknown' && !confirmResend) {
      return refuse(409, 'delivery_unknown')
    }
  }
  const outcome = state.sendOutcome ?? { state: 'sent' }
  const send = {
    send_id: `send-${key}-${String(nextUid++)}`,
    code: null,
    filed: outcome.state === 'sent' ? true : null,
    rejected: [],
    notes: [],
    note_codes: [],
    settled: outcome.state === 'sent',
    ...outcome,
    polls: state.sendingPolls ?? 0,
    filingPolls: state.filingPolls ?? 0,
  }
  state.sends[key] = send
  if (send.state === 'sent') {
    state.delivered = [...(state.delivered ?? []), { key, ref: draft.ref, text: draft.text }]
    state.drafts = state.drafts?.filter((each) => each !== draft)
    unlistDraft(state, draft)
  }
  return json(202, { send_id: send.send_id })
}

/** A retry for a draft that is gone reaches only a send keyed by its ref: one written elsewhere. */
function goneSendAnswer(state: FakeMailState, ref: string, confirmResend: boolean): Response {
  const existing = state.sends?.[ref]
  if (existing?.state === 'sent') {
    return json(200, existing)
  }
  if (existing?.state === 'sending') {
    return json(202, { send_id: existing.send_id })
  }
  return existing?.state === 'unknown' && !confirmResend ? refuse(409, 'delivery_unknown') : notFound()
}

function sendResult(state: FakeMailState, id: string): Response {
  const send = Object.values(state.sends ?? {}).find((each) => each.send_id === id)
  if (send === undefined) {
    return notFound()
  }
  if (send.polls > 0) {
    send.polls -= 1
    return json(200, { ...send, state: 'sending', filed: null, notes: [], note_codes: [], settled: false })
  }
  if (send.state === 'sent' && (send.filingPolls ?? 0) > 0) {
    send.filingPolls = (send.filingPolls ?? 0) - 1
    return json(200, { ...send, filed: null, notes: [], note_codes: [], settled: false })
  }
  return json(200, send)
}

/** The slice C routes over `state`, or `null` for another route. */
function composeRoutes(state: FakeMailState, url: URL, init?: RequestInit): Response | null {
  const path = url.pathname
  const method = init?.method ?? 'GET'
  const text = typeof init?.body === 'string' ? init.body : '{}'
  if (path === '/api/v1/mail/drafts/prefill' && method === 'POST') {
    return prefillAnswer(state, JSON.parse(text) as { ref: string; mode: string })
  }
  if (path === '/api/v1/mail/drafts' && method === 'POST') {
    return saveDraftAnswer(state, init?.body as FormData)
  }
  if (path === '/api/v1/mail/drafts/find') {
    const composeId = url.searchParams.get('compose_id')
    const draft = (state.drafts ?? []).findLast(
      (each) =>
        each.compose_id === composeId &&
        each.account === url.searchParams.get('account') &&
        state.unindexed?.includes(each.ref) !== true,
    )
    return draft === undefined ? notFound() : json(200, { ref: draft.ref, parts: draft.parts })
  }
  let match = /^\/api\/v1\/mail\/sends\/([^/]+)$/.exec(path)
  if (match !== null) {
    return sendResult(state, decodeURIComponent(match[1] ?? ''))
  }
  match = /^\/api\/v1\/mail\/drafts\/([^/]+?)(\/send)?$/.exec(path)
  if (match === null) {
    return null
  }
  const ref = decodeURIComponent(match[1] ?? '')
  const draft = state.drafts?.find((each) => each.ref === ref)
  const sending = match[2] !== undefined && method === 'POST'
  const confirmResend = sending && (JSON.parse(text) as { confirm_resend?: boolean }).confirm_resend === true
  if (draft === undefined) {
    return sending ? goneSendAnswer(state, ref, confirmResend) : notFound()
  }
  if (sending) {
    return sendAnswer(state, draft, confirmResend)
  }
  if (method === 'DELETE') {
    if (isOff(state, draft.account)) {
      return refuse(409, 'send_disabled')
    }
    state.drafts = state.drafts?.filter((each) => each !== draft)
    unlistDraft(state, draft)
    return json(200, { removed: 'expunged' })
  }
  return json(200, draft)
}

/** The folder writes over `state.folders` (`/` the delimiter), or `null` for another route. */
function folderRoutes(state: FakeMailState, url: URL, init?: RequestInit): Response | null {
  const match = /^\/api\/v1\/mail\/accounts\/([^/]+)\/folders(?:\/(rename|delete))?$/.exec(url.pathname)
  if (match === null || init?.method !== 'POST') {
    return null
  }
  const account = decodeURIComponent(match[1] ?? '')
  const folders = state.folders[account] ?? []
  const body = JSON.parse(typeof init.body === 'string' ? init.body : '{}') as {
    parent?: string | null
    name?: string
    folder?: string
    messages?: number
  }
  const target = (): string => (body.parent == null ? String(body.name) : `${body.parent}/${String(body.name)}`)
  if (match[2] === undefined) {
    const path = target()
    if (folders.some((each) => each.path === path)) {
      return refuse(409, 'folder_exists')
    }
    state.folders[account] = [...folders, { path, role: null, unread: 0, total: 0 }]
    return json(201, { account_id: account, path })
  }
  const from = String(body.folder)
  const folder = folders.find((each) => each.path === from)
  if (folder === undefined) {
    return refuse(404, 'mailbox_missing')
  }
  if (match[2] === 'rename') {
    const to = target()
    const moved = (path: string): string => (path === from || path.startsWith(`${from}/`) ? to + path.slice(from.length) : path)
    state.folders[account] = folders.map((each) => ({ ...each, path: moved(each.path) }))
    state.messages = Object.fromEntries(
      Object.entries(state.messages).map(([key, items]) => [key.startsWith(`${account}/`) ? `${account}/${moved(key.slice(account.length + 1))}` : key, items]),
    )
    return json(200, { account_id: account, folder: from, path: to, rule_sets: [], filters: [], rule_sets_updated: true })
  }
  if (body.messages !== folder.total) {
    return json(409, {
      detail: { code: 'folder_changed', message: 'The folder changed since it was read.', messages: folder.total },
    })
  }
  state.folders[account] = folders.filter((each) => each !== folder)
  return json(200, { account_id: account, folder: from, messages: folder.total })
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
            send_mode: state.sendModes?.[id] ?? 'drafts',
            imap: endpoint,
            smtp: endpoint,
            identities: state.identities?.[id] ?? [],
            auto_approve: [],
            has_password: true,
            run_filters: false,
            attachment_max_mb: state.attachmentMaxMb ?? 10,
            max_recipients: state.maxRecipients ?? 10,
          }
        }),
      )
    }
    const composed = composeRoutes(state, url, init)
    if (composed !== null) {
      return composed
    }
    const folderWrite = folderRoutes(state, url, init)
    if (folderWrite !== null) {
      return folderWrite
    }
    let match = /^\/api\/v1\/mail\/accounts\/([^/]+)\/(folders|messages)$/.exec(path)
    if (match !== null) {
      const account = decodeURIComponent(match[1] ?? '')
      if (!state.accounts.includes(account)) {
        return notFound()
      }
      if (match[2] === 'folders') {
        return json(200, { account_id: account, folders: (state.folders[account] ?? []).map(listedFolder) })
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
    match = /^\/api\/v1\/mail\/messages\/(move|move-to-account|archive|trash|flag)$/.exec(path)
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
    const answer = respond(url, init)
    if (state.lose?.(request) === true) {
      throw new TypeError('Failed to fetch')
    }
    return answer
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** A draft of the fake's, with the fields a test cares about. */
export function fakeDraft(account: string, uid: number, fields: Partial<Draft> = {}): Draft {
  return {
    ref: fakeRef(account, 'Drafts', uid),
    account,
    compose_id: `00000000-0000-4000-8000-${String(uid).padStart(12, '0')}`,
    from: null,
    to: [],
    cc: [],
    bcc: [],
    subject: `Draft ${String(uid)}`,
    text: `Text of draft ${String(uid)}`,
    answers: null,
    lossy: false,
    parts: [],
    send: null,
    ...fields,
  }
}

/** Each save's `draft` part and the names of the files it carried, in order. */
export function savesOf(fetchMock: Mock): { fields: DraftFields; files: string[]; keepalive: boolean }[] {
  return fetchMock.mock.calls
    .filter(([callPath, init]) => {
      const { pathname } = new URL(callPath as string, 'http://mailroom.test')
      return pathname === '/api/v1/mail/drafts' && (init as RequestInit | undefined)?.method === 'POST'
    })
    .map(([, init]) => {
      const { body, keepalive } = init as RequestInit
      const form = body as FormData
      return {
        fields: JSON.parse(form.get('draft') as string) as DraftFields,
        files: (form.getAll('files[]') as File[]).map((file) => file.name),
        keepalive: keepalive === true,
      }
    })
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

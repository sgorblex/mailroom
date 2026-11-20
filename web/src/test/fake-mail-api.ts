import { vi, type Mock } from 'vitest'

import type { AccountId, AccountColour, Identity } from '@/api/accounts.ts'
import type { Filter, RuleSet } from '@/api/filters.ts'
import type {
  Draft,
  DraftFields,
  IndexFolder,
  MailFolder,
  MessageItem,
  MessagePage,
  MessagePart,
  MessageView,
  MutedThread,
  OrganizeGroup,
  SendResult,
  SourceFailure,
} from '@/api/mail.ts'

type Defaulted = 'delimiter' | 'selectable' | 'uid_validity' | 'uid_next'
export type FakeFolder = Omit<MailFolder, Defaulted> & Partial<Pick<MailFolder, Defaulted>>

/** A fake folder as the API lists it: UIDVALIDITY 1 and UIDNEXT 1000 unless it says otherwise. */
export function listedFolder(folder: FakeFolder): MailFolder {
  return { delimiter: '/', selectable: true, uid_validity: 1, uid_next: 1000, ...folder }
}

/** The accounts the tests use, and the names the fake lists them under. */
export const WORK = 1
export const HOME = 2
const ACCOUNT_NAMES: Record<AccountId, string> = { [WORK]: 'work', [HOME]: 'home' }

export interface FakeMailState {
  accounts: AccountId[]
  /** Each account's folders; `delimiter` is `/` and `selectable` true unless they say otherwise (`listedFolder`). */
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
  /** Moves answer no `new_refs`, as from a server without COPYUID. */
  noNewRefs?: boolean
  /** Moves answer one `new_refs` fewer than they moved. */
  shortNewRefs?: boolean
  /** `"<account>/<folder path>"` → the code its group fails with after copying, the messages in both. */
  copiedNotRemoved?: Record<string, string>
  /** Called with each request (`"POST /api/v1/mail/messages/flag"`, its body); the answer waits for the promise it returns. */
  hold?: (request: string, body: unknown) => Promise<void> | undefined
  /** Whether each account's server keeps keywords (To sort); `null` unless it says otherwise. */
  keywords?: Record<string, boolean | null>
  /** Each account's send mode; `drafts` unless it says otherwise. */
  sendModes?: Record<string, string>
  /** Each account's name and colour; `ACCOUNT_NAMES`' unless it says otherwise (else unnamed and shown by its login), without a colour. */
  names?: Partial<Record<AccountId, { name: string; colour: AccountColour | null }>>
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
  /**
   * What `/messages/count` and `/messages/bulk` match, whatever the scope: `total` messages of
   * `folder` (INBOX unless it says otherwise) of WORK, of which each bulk call acts on up to 500.
   */
  /** `changing`: with a flag change as the action, how many it would change (else `total`). */
  matching?: { total: number; changing?: number; folder?: string; failures?: SourceFailure[] }
  /** Each account's Run filters; off unless it says otherwise. */
  runFilters?: Record<string, boolean>
  /** The rule sets and filters the filter routes keep, for what the mail screen offers to add. */
  ruleSets?: RuleSet[]
  filters?: Filter[]
  /** The muted threads the mute routes keep, one per muted message but for `conversations`. */
  muted?: MutedThread[]
  /** Refs of one conversation each, whose mutes answer the one muted thread. */
  conversations?: string[][]
  /** The muted thread each muted message's mute answered, by ref. */
  mutedBy?: Record<string, number>
  /** Each account's folders as the index settings list them; an account without is 404 there. */
  index?: Record<string, { gmail?: boolean; folders: IndexFolder[] }>
  /** The user's own age limit of the index, which `PUT /auth/me/index-max-age` sets; none unless it says otherwise. */
  indexMaxAgeDays?: number | null
  /** The instance's cap on it; none unless it says otherwise. */
  indexMaxAgeCap?: number | null
  /** Each account's folders whose new mail notifies, which `PUT /mail/notifications/accounts/{id}` sets; INBOX unless it says otherwise. */
  notifications?: Record<string, string[]>
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
function fakeRef(account: AccountId, folder: string, uid: number): string {
  return `ref-${String(account)}-${folder}-${String(uid)}`
}

/** The uid `fakeRef` put in the ref of `item`. */
function uidOf(item: MessageItem): number {
  return Number(/-(\d+)$/.exec(item.ref)?.[1] ?? Number.NaN)
}

/** A list item; `ref` is made up from the account, folder and uid (`fakeRef`). */
export function messageItem(account: AccountId, folder: string, uid: number, fields: Partial<MessageItem> = {}): MessageItem {
  return {
    ref: fakeRef(account, folder, uid),
    account_id: account,
    folder,
    date: '2026-10-30T09:12:00+00:00',
    from: { name: `Sender ${String(uid)}`, address: `sender${String(uid)}@example.org` },
    subject: `Message ${String(uid)}`,
    flags: { seen: true, flagged: false, answered: false, forwarded: false, draft: false, to_sort: false },
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
    remote_images: 0,
    loadable_remote_images: 0,
    attachments: [],
    calendar: null,
    headers: {
      mailing_list: null,
      authentication: [],
      dkim_signatures: [],
      from_fields: 1,
      delivered_to: [],
      truncated: false,
    },
    sender_mismatch: false,
    sent_to: null,
    authentication: {
      verified: false,
      spf: null,
      spf_domain: null,
      dkim: null,
      dkim_domain: null,
      dmarc: null,
      dmarc_pass: false,
      from_domain: null,
    },
    remote_trusted: false,
    one_click_unsubscribe: false,
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
  body: {
    refs: string[]
    folder?: string
    account?: AccountId
    removal?: string
    seen?: boolean
    flagged?: boolean
    to_sort?: boolean
  },
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
    const { account_id: account, folder } = items[0] ?? messageItem(0, '', 0)
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
          {
            ...item.flags,
            seen: body.seen ?? item.flags.seen,
            flagged: body.flagged ?? item.flags.flagged,
            to_sort: body.to_sort ?? item.flags.to_sort,
          },
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
      return {
        ...group,
        new_refs: [],
        flags: [...changed].map(([ref, flags]) => ({
          ref,
          seen: flags.seen,
          flagged: flags.flagged,
          to_sort: flags.to_sort,
        })),
      }
    }
    const role = route === 'archive' || route === 'junk' || route === 'trash' ? route : null
    const destination = role === null ? body.folder : state.folders[account]?.find((each) => each.role === role)?.path
    if (destination === undefined) {
      return failed('unsupported')
    }
    // A move to another account lands there; any other move stays in the account.
    const to = route === 'move-to-account' ? (body.account ?? account) : account
    // New uids above the destination's, as a server gives them, so its list stays in uid order.
    const there = state.messages[`${String(to)}/${destination}`] ?? []
    const top = Math.max(0, ...there.map(uidOf))
    const moved = items.map((item, index) => ({
      ...item,
      ref: fakeRef(to, destination, top + items.length - index),
      account_id: to,
      folder: destination,
    }))
    const newRefs = moved.map((item) => item.ref)
    const copiedOnly = state.copiedNotRemoved?.[key]
    state.messages[`${String(to)}/${destination}`] = [...moved, ...there]
    if (copiedOnly !== undefined) {
      // Copied there, but still here: the group fails with the copies' refs.
      return { ...failed(copiedOnly), destination_account: to, new_refs: newRefs }
    }
    state.messages[key] = (state.messages[key] ?? []).filter((item) => !items.includes(item))
    return {
      ...group,
      new_refs: state.noNewRefs === true ? null : state.shortNewRefs === true ? newRefs.slice(1) : newRefs,
      destination_account: route === 'move-to-account' ? to : null,
    }
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

function defaultIdentity(state: FakeMailState, account: AccountId): string | null {
  const identities = state.identities?.[account] ?? []
  return (identities.find((each) => each.default === true) ?? identities[0])?.address ?? null
}

function isOff(state: FakeMailState, account: AccountId): boolean {
  return state.sendModes?.[account] === 'off'
}

function deliverable(address: string): boolean {
  return /^[!-~]+@[!-~]+$/.test(address)
}

/** The account's Drafts list holds the draft under its new ref, as the server's folder would. */
function listDraft(state: FakeMailState, draft: Draft, previous: string | null): void {
  const key = `${String(draft.account)}/Drafts`
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
  const key = `${String(draft.account)}/Drafts`
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
    forwards: fields.forwards,
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
  if (body.mode === 'unsubscribe') {
    const mailto = view.headers.mailing_list?.unsubscribe_mailto
    if (mailto === null || mailto === undefined) {
      return refuse(400, 'no_unsubscribe_address')
    }
    const url = new URL(mailto)
    return json(200, {
      account: view.account_id,
      from: view.sent_to ?? defaultIdentity(state, view.account_id),
      to: [{ name: '', address: url.pathname }],
      cc: [],
      bcc: [],
      subject: url.searchParams.get('subject') ?? 'unsubscribe',
      text: url.searchParams.get('body') ?? '',
      answers: null,
      forwards: null,
      attachments: [],
    })
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
    forwards: forward ? view.ref : null,
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
        String(each.account) === url.searchParams.get('account') &&
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
  const match = /^\/api\/v1\/mail\/accounts\/([^/]+)\/folders(?:\/(rename|delete|mark-read))?$/.exec(url.pathname)
  if (match === null || init?.method !== 'POST') {
    return null
  }
  const account = Number(match[1])
  const folders = state.folders[account] ?? []
  const body = JSON.parse(typeof init.body === 'string' ? init.body : '{}') as {
    parent?: string | null
    name?: string
    folder?: string
    messages?: number
    uid_validity?: number
    uid_next?: number
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
  if (match[2] === 'mark-read') {
    if (body.uid_validity !== listedFolder(folder).uid_validity) {
      return refuse(409, 'stale_reference')
    }
    let marked = 0
    for (const item of state.messages[`${String(account)}/${from}`] ?? []) {
      if (!item.flags.seen && uidOf(item) < Number(body.uid_next)) {
        item.flags = { ...item.flags, seen: true }
        marked += 1
      }
    }
    folder.unread = Math.max(0, folder.unread - marked)
    return json(200, { account_id: account, folder: from, marked })
  }
  if (match[2] === 'rename') {
    const to = target()
    const moved = (path: string): string => (path === from || path.startsWith(`${from}/`) ? to + path.slice(from.length) : path)
    state.folders[account] = folders.map((each) => ({ ...each, path: moved(each.path) }))
    state.messages = Object.fromEntries(
      Object.entries(state.messages).map(([key, items]) => [key.startsWith(`${String(account)}/`) ? `${String(account)}/${moved(key.slice(String(account).length + 1))}` : key, items]),
    )
    return json(200, { account_id: account, folder: from, path: to, rule_sets: [], filters: [], rule_sets_updated: true, all_accounts_rule_sets: [] })
  }
  if (body.messages !== folder.total) {
    return json(409, {
      detail: { code: 'folder_changed', message: 'The folder changed since it was read.', messages: folder.total },
    })
  }
  state.folders[account] = folders.filter((each) => each !== folder)
  return json(200, { account_id: account, folder: from, messages: folder.total })
}

/** A folder off the index, as the index settings list it. */
export function indexFolder(path: string, fields: Partial<IndexFolder> = {}): IndexFolder {
  return {
    path,
    role: null,
    indexed_as: 'folder',
    enabled: false,
    total: 0,
    state: null,
    error: null,
    indexed: null,
    unread: null,
    oldest_indexed: null,
    last_synced_at: null,
    ...fields,
  }
}

/** The index settings' routes and the user's age limit over `state.index`; `null` for another path. */
function indexRoutes(state: FakeMailState, url: URL, init?: RequestInit): Response | null {
  if (url.pathname === '/api/v1/auth/me/index-max-age' && init?.method === 'PUT') {
    const { index_max_age_days } = JSON.parse(init.body as string) as { index_max_age_days: number | null }
    state.indexMaxAgeDays = index_max_age_days
    return json(200, { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days })
  }
  const match = /^\/api\/v1\/mail\/index\/accounts\/(\d+)(\/folders)?$/.exec(url.pathname)
  if (match === null) {
    return null
  }
  const index = state.index?.[match[1] ?? '']
  if (index === undefined) {
    return notFound()
  }
  if (match[2] === undefined) {
    const limits = [state.indexMaxAgeDays ?? null, state.indexMaxAgeCap ?? null].filter((days) => days !== null)
    return json(200, {
      account_id: Number(match[1]),
      gmail: index.gmail ?? false,
      max_age_days: limits.length === 0 ? null : Math.min(...limits),
      max_age_cap_days: state.indexMaxAgeCap ?? null,
      folders: index.folders,
    })
  }
  const { folder, enabled } = JSON.parse(init?.body as string) as { folder: string; enabled: boolean }
  const switched = (each: IndexFolder): IndexFolder =>
    enabled
      ? { ...each, enabled, state: 'pending', indexed: 0, unread: 0 }
      : indexFolder(each.path, { role: each.role, indexed_as: each.indexed_as, total: each.total })
  index.folders = index.folders.map((each) => (each.path === folder ? switched(each) : each))
  return json(200, { account_id: Number(match[1]), folder, enabled })
}

/** The desktop notifications' folder choice over `state.notifications`; `null` for another path. */
function notificationRoutes(state: FakeMailState, url: URL, init?: RequestInit): Response | null {
  const chosen = (account: AccountId): { account_id: AccountId; folders: string[] } => ({
    account_id: account,
    folders: state.notifications?.[account] ?? ['INBOX'],
  })
  if (url.pathname === '/api/v1/mail/notifications') {
    return json(200, { accounts: state.accounts.map(chosen) })
  }
  const match = /^\/api\/v1\/mail\/notifications\/accounts\/(\d+)$/.exec(url.pathname)
  if (match === null || init?.method !== 'PUT') {
    return null
  }
  const account = Number(match[1])
  if (!state.accounts.includes(account)) {
    return notFound()
  }
  const { folders } = JSON.parse(init.body as string) as { folders: string[] }
  state.notifications = { ...state.notifications, [account]: folders }
  return json(200, chosen(account))
}

/** Counting and acting on `state.matching`, as the bulk routes do: refused unless `expected` is the count. */
function matchingRoutes(state: FakeMailState, bulk: boolean, body: { expected?: number; action?: { kind: string } }): Response {
  const found = state.matching ?? { total: 0 }
  const failures = found.failures ?? []
  // A flag change counts, and acts on, those it would change.
  const flag = body.action?.kind === 'flag' && found.changing !== undefined
  const matching = { ...found, total: flag ? (found.changing ?? 0) : found.total }
  const left = (count: number): void => {
    if (flag) {
      found.changing = count
    } else {
      found.total = count
    }
  }
  if (!bulk) {
    return json(200, { total: matching.total, failures })
  }
  if (matching.total > 5000 || body.expected !== matching.total) {
    const code = matching.total > 5000 ? 'too_many' : 'count_changed'
    return json(409, { detail: { code, message: `Server text for ${code}`, matching: matching.total } })
  }
  const group: OrganizeGroup = {
    account: WORK,
    folder: matching.folder ?? 'INBOX',
    ok: true,
    count: Math.min(500, matching.total),
    new_refs: [],
    destination_account: null,
    junk: null,
    flags: [],
    error: null,
  }
  const failed = state.groupFailures?.[`${String(WORK)}/${group.folder}`]
  if (failed !== undefined) {
    const error = { code: failed, message: `Server text for ${failed}` }
    return json(200, { done: 0, remaining: matching.total, results: [{ ...group, ok: false, count: 0, error }], failures })
  }
  left(matching.total - group.count)
  return json(200, { done: group.count, remaining: matching.total - group.count, results: [group], failures })
}

/** The rule set, filter and Run filters routes, as far as the mail screen calls them; `null` for another path. */
function filterRoutes(state: FakeMailState, path: string, init?: RequestInit): Response | null {
  const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {}
  const ruleSets = (state.ruleSets ??= [])
  let match = /^\/api\/v1\/accounts\/(\d+)\/(rule-sets|filter-settings)$/.exec(path)
  if (match !== null) {
    const account = Number(match[1])
    if (match[2] === 'filter-settings') {
      state.runFilters = { ...state.runFilters, [account]: body['run_filters'] === true }
      return json(200, { run_filters: body['run_filters'] === true })
    }
    if (init?.method === 'POST') {
      const made = { ...(body as Omit<RuleSet, 'id' | 'account_id'>), id: 1000 + ruleSets.length, account_id: account }
      ruleSets.push(made)
      return json(201, made)
    }
    return json(200, ruleSets.filter((ruleSet) => ruleSet.account_id === account))
  }
  match = /^\/api\/v1\/rule-sets\/(\d+)(\/filters)?$/.exec(path)
  const ruleSet = ruleSets.find((each) => each.id === Number(match?.[1]))
  if (match === null || ruleSet === undefined) {
    return null
  }
  if (match[2] !== undefined) {
    const filters = (state.filters ??= [])
    const filter: Filter = {
      ...(body as Pick<Filter, 'name' | 'enabled' | 'spec'>),
      id: filters.length + 1,
      rule_set_id: ruleSet.id,
      position: 0,
      updated_at: '2026-11-19T10:00:00+00:00',
      matches_30d: 0,
    }
    filters.push(filter)
    return json(201, filter)
  }
  Object.assign(ruleSet, body)
  return json(200, ruleSet)
}

/**
 * A fake mail API over `state`: `/api/v1/accounts`, the `/api/v1/mail` routes and the index's age limit. A cursor is
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
          const endpoint = { host: `imap.${String(id)}.example`, port: 993, tls: 'implicit' }
          return {
            id,
            name: state.names?.[id]?.name ?? ACCOUNT_NAMES[id] ?? null,
            display_name: state.names?.[id]?.name ?? ACCOUNT_NAMES[id] ?? `me@${String(id)}.example`,
            colour: state.names?.[id]?.colour ?? null,
            authserv_id: null,
            login: `me@${String(id)}.example`,
            send_mode: state.sendModes?.[id] ?? 'drafts',
            imap: endpoint,
            smtp: endpoint,
            identities: state.identities?.[id] ?? [],
            auto_approve: [],
            has_password: true,
            run_filters: state.runFilters?.[id] ?? false,
            attachment_max_mb: state.attachmentMaxMb ?? 10,
            max_recipients: state.maxRecipients ?? 10,
            keywords: state.keywords?.[id] ?? null,
          }
        }),
      )
    }
    const indexed = indexRoutes(state, url, init) ?? notificationRoutes(state, url, init)
    if (indexed !== null) {
      return indexed
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
      const account = Number(match[1])
      if (!state.accounts.includes(account)) {
        return notFound()
      }
      if (match[2] === 'folders') {
        return json(200, { account_id: account, folders: (state.folders[account] ?? []).map(listedFolder) })
      }
      const folder = query.get('folder') ?? 'INBOX'
      const own = state.messages[`${String(account)}/${folder}`]
      // With its subfolders: every folder under it ('/' as delimiter) merged, by UID.
      const all =
        own !== undefined && query.get('subfolders') === 'true'
          ? Object.entries(state.messages)
              .filter(([key]) => key === `${String(account)}/${folder}` || key.startsWith(`${String(account)}/${folder}/`))
              .flatMap(([, items]) => items)
              .sort((a, b) => uidOf(b) - uidOf(a))
          : own
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
    const filtersAnswer = filterRoutes(state, path, init)
    if (filtersAnswer !== null) {
      return filtersAnswer
    }
    match = /^\/api\/v1\/mail\/messages\/(move|move-to-account|archive|junk|trash|flag)$/.exec(path)
    if (match !== null && init?.method === 'POST' && typeof init.body === 'string') {
      return organize(state, match[1] ?? '', JSON.parse(init.body) as Parameters<typeof organize>[2])
    }
    if (path === '/api/v1/mail/messages/count' || path === '/api/v1/mail/messages/bulk') {
      return matchingRoutes(state, path.endsWith('bulk'), JSON.parse(init?.body as string) as { expected?: number; action?: { kind: string } })
    }
    if (path === '/api/v1/mail/remote-senders' && init?.method === 'POST') {
      // Remembered when DMARC passed for the message's From; its views then say so.
      const { ref } = JSON.parse(init.body as string) as { ref: string }
      const view = state.views[ref]
      if (view === undefined) {
        return notFound()
      }
      const address = view.from?.address
      if (!view.authentication.dmarc_pass || address === undefined) {
        return json(400, refusal(400, 'sender_not_verified').body)
      }
      for (const each of Object.values(state.views)) {
        if (each.from?.address === address && each.authentication.dmarc_pass) {
          each.remote_trusted = true
        }
      }
      return json(201, { address, created_at: '2026-11-19T10:00:00+00:00' })
    }
    match = /^\/api\/v1\/mail\/messages\/([^/]+)\/mute$/.exec(path)
    if (match !== null && init?.method === 'POST') {
      const view = state.views[decodeURIComponent(match[1] ?? '')]
      if (view === undefined) {
        return notFound()
      }
      const muted = (state.muted ??= [])
      const ref = view.ref
      const mutedBy = (state.mutedBy ??= {})
      const conversation = state.conversations?.find((refs) => refs.includes(ref)) ?? [ref]
      const joined = muted.find((each) => conversation.some((other) => mutedBy[other] === each.id))
      if (joined !== undefined) {
        mutedBy[ref] = joined.id
        return json(201, joined)
      }
      const thread: MutedThread = {
        id: Math.max(0, ...muted.map((each) => each.id)) + 1,
        account_id: view.account_id,
        ids: 1,
        gmail: false,
        run_filters: state.runFilters?.[view.account_id] ?? false,
        created_at: '2026-11-19T10:00:00+00:00',
      }
      muted.push(thread)
      mutedBy[ref] = thread.id
      return json(201, thread)
    }
    match = /^\/api\/v1\/mail\/muted\/(\d+)$/.exec(path)
    if (match !== null && init?.method === 'DELETE') {
      const before = state.muted ?? []
      state.muted = before.filter((thread) => thread.id !== Number(match?.[1]))
      return state.muted.length === before.length
        ? json(404, { detail: { code: 'not_found', message: 'No such muted thread' } })
        : new Response(null, { status: 204 })
    }
    match = /^\/api\/v1\/mail\/messages\/([^/]+)\/unsubscribe$/.exec(path)
    if (match !== null && init?.method === 'POST') {
      // Posted when the read said it would be, as the server checks it again.
      const view = state.views[decodeURIComponent(match[1] ?? '')]
      if (view === undefined) {
        return notFound()
      }
      if (!view.one_click_unsubscribe) {
        return refuse(400, 'unsubscribe_not_signed')
      }
      return json(200, { outcome: 'ok', status: 200, list_id: view.headers.mailing_list?.id ?? null })
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
export function fakeDraft(account: AccountId, uid: number, fields: Partial<Draft> = {}): Draft {
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
    forwards: null,
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

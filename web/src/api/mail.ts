import { z } from 'zod'

import { type AccountId, accountIdSchema } from './accounts.ts'
import { getApiBaseUrl } from './api-base-url.ts'
import {
  ApiRequestError,
  apiDelete,
  apiDeleteJson,
  apiGetJson,
  apiPatchJson,
  apiPostForm,
  apiPostJson,
  apiPutJson,
} from './http.ts'

/** The REST contract of `docs/requirements/mail-client.md`. Refs and cursors are opaque strings. */

export const FOLDER_ROLES = ['inbox', 'sent', 'drafts', 'trash', 'junk', 'archive', 'all', 'flagged'] as const
export type FolderRole = (typeof FOLDER_ROLES)[number]

const addressSchema = z.object({ name: z.string(), address: z.string() })
export type MailAddress = z.infer<typeof addressSchema>

const flagsSchema = z.object({
  seen: z.boolean(),
  flagged: z.boolean(),
  answered: z.boolean(),
  /** `$Forwarded`, set when a forward of the message is sent. */
  forwarded: z.boolean(),
  draft: z.boolean(),
  /** The To sort mark (`$MailroomSort`). */
  to_sort: z.boolean(),
})

const mailFolderSchema = z.object({
  path: z.string(),
  role: z.enum(FOLDER_ROLES).nullable(),
  unread: z.number(),
  total: z.number(),
  /** The account's hierarchy delimiter; `null` on a server without one. */
  delimiter: z.string().nullable(),
  /** `false` for a level that holds only folders (`\Noselect`): no messages, counts 0. */
  selectable: z.boolean(),
  /**
   * STATUS's, what a mark-read sends back; `null` for a `\Noselect` folder, one whose STATUS failed
   * and a Gmail label's counted from the index.
   */
  uid_validity: z.number().nullable(),
  uid_next: z.number().nullable(),
})
export type MailFolder = z.infer<typeof mailFolderSchema>

const folderListSchema = z.object({ account_id: accountIdSchema, folders: z.array(mailFolderSchema) })

const messageItemSchema = z.object({
  ref: z.string(),
  account_id: accountIdSchema,
  folder: z.string(),
  date: z.string().nullable(),
  from: addressSchema,
  subject: z.string(),
  flags: flagsSchema,
  has_attachments: z.boolean(),
  preview: z.string(),
  size: z.number(),
  /**
   * The folder the row was listed under when not its own: a Gmail row read from the index names
   * All Mail, listed under INBOX or a label's folder, where an action on it acts (`sourceFolders`).
   */
  listed_folder: z.string().nullable().optional(),
})
/** A listed message; in a page grouped by conversation, `conversation` is the one it stands for. */
export type MessageItem = z.infer<typeof messageItemSchema> & { conversation?: Conversation }

const failureSchema = z.object({ account_id: accountIdSchema, folder: z.string().nullable(), code: z.string() })
export type SourceFailure = z.infer<typeof failureSchema>

/** A page's message as the conversation it stands for, its latest message (`group=thread`). */
const conversationSchema = z.object({
  count: z.number(),
  unread: z.number(),
  /** The first senders' names, first written first, at most four. */
  participants: z.array(z.string()),
  has_attachments: z.boolean(),
  flagged: z.boolean(),
  /**
   * What an action on it takes, newest first: every message of it in the source read but the
   * user's own copies (Sent, Drafts), unless it holds nothing else.
   */
  refs: z.array(z.string()),
  /** The folder each of `refs` was listed under, where not its own (as a message's `listed_folder`). */
  listed_folders: z.record(z.string(), z.string()).default({}),
  /** Its latest sender neither wrote nor was written to in its other messages. */
  latest_from_new_sender: z.boolean().default(false),
})
export type Conversation = z.infer<typeof conversationSchema>

const pageFields = z.object({
  messages: z.array(messageItemSchema),
  next_cursor: z.string().nullable(),
  failures: z.array(failureSchema),
  /** One for each message when the page is grouped, else null (or absent). */
  conversations: z.array(conversationSchema).nullable().default(null),
})

/** Each message of a grouped page carries its conversation (`conversation`), so rows loaded later keep it. */
function withConversations<T extends z.infer<typeof pageFields>>(
  page: T,
): Omit<T, 'messages'> & { messages: (MessageItem & { conversation?: Conversation })[] } {
  const { conversations } = page
  return conversations === null
    ? page
    : { ...page, messages: page.messages.map((message, index) => ({ ...message, conversation: conversations[index] })) }
}

const messagePageSchema = pageFields.transform(withConversations)
/** A page, grouped when `conversations` is there (and not null). */
export type MessagePage = Omit<z.infer<typeof messagePageSchema>, 'conversations'> & { conversations?: Conversation[] | null }

const partSchema = z.object({ part: z.string(), name: z.string(), type: z.string(), size: z.number() })
export type MessagePart = z.infer<typeof partSchema>

/** The list, authentication and delivery fields as the server parsed them: the sender's or a relay's words, unverified. */
const headerInfoSchema = z.object({
  mailing_list: z
    .object({
      id: z.string().nullable(),
      name: z.string().nullable(),
      archive: z.string().nullable(),
      post: z.string().nullable(),
      help: z.string().nullable(),
      unsubscribe_https: z.string().nullable(),
      unsubscribe_mailto: z.string().nullable(),
      one_click: z.boolean(),
      unsubscribe_fields: z.number(),
      unsubscribe_post_fields: z.number(),
    })
    .nullable(),
  /** Topmost first. */
  authentication: z.array(
    z.object({
      authserv_id: z.string().nullable(),
      results: z.array(
        z.object({
          method: z.string(),
          result: z.string(),
          header_d: z.string().nullable(),
          header_b: z.string().nullable(),
          header_from: z.string().nullable(),
        }),
      ),
    }),
  ),
  dkim_signatures: z.array(z.object({ d: z.string(), b: z.string(), h: z.array(z.string()) })),
  from_fields: z.number(),
  delivered_to: z.array(z.string()),
  truncated: z.boolean(),
})

const messageViewSchema = z.object({
  ref: z.string(),
  account_id: accountIdSchema,
  folder: z.string(),
  from: addressSchema.nullable(),
  to: z.array(addressSchema),
  cc: z.array(addressSchema),
  reply_to: z.array(addressSchema),
  date: z.string().nullable(),
  subject: z.string(),
  message_id: z.string().nullable(),
  flags: flagsSchema,
  text: z.object({
    content: z.string(),
    removed_chars: z.number(),
    incomplete: z.boolean(),
    next_offset: z.number().nullable(),
    quoted_omitted: z.boolean(),
  }),
  has_html: z.boolean(),
  /** Remote images the HTML asks for, blocked until the reader shows them. */
  remote_images: z.number().int().nonnegative(),
  /** Of those, the ones `remote=1` shows: the others stay blocked. */
  loadable_remote_images: z.number().int().nonnegative(),
  attachments: z.array(partSchema),
  calendar: partSchema.nullable(),
  headers: headerInfoSchema,
  /** The From display name names another address or domain than `from.address`. */
  sender_mismatch: z.boolean(),
  /** The account's identity the message arrived at. */
  sent_to: z.string().nullable(),
  /** The account's own authentication server's verdict: all null unless `verified`. */
  authentication: z.object({
    verified: z.boolean(),
    spf: z.string().nullable(),
    spf_domain: z.string().nullable(),
    dkim: z.string().nullable(),
    dkim_domain: z.string().nullable(),
    dmarc: z.string().nullable(),
    dmarc_pass: z.boolean(),
    from_domain: z.string().nullable(),
  }),
  /** The user shows this sender's remote images without asking, and DMARC passed for its one From. */
  remote_trusted: z.boolean(),
  /** `unsubscribe` would post: the account's own authentication server vouches for the list's one-click fields. */
  one_click_unsubscribe: z.boolean(),
  /** The view it opens in: `html` when it has HTML, unless the user reads its sender as text. */
  preferred_view: z.enum(['html', 'text']),
})
export type MessageView = z.infer<typeof messageViewSchema>

/** The search fields; an absent one is not a criterion. `account` and `folder` take `*` for all. */
export interface SearchQuery {
  account?: string
  folder?: string
  from?: string
  to?: string
  subject?: string
  text?: string
  since?: string
  before?: string
  unread?: boolean
  flagged?: boolean
  has_attachment?: boolean
  to_sort?: boolean
  gmail_query?: string
}

export const SEARCH_TEXT_FIELDS = [
  'account',
  'folder',
  'from',
  'to',
  'subject',
  'text',
  'since',
  'before',
  'gmail_query',
] as const
export const SEARCH_FLAG_FIELDS = ['unread', 'flagged', 'to_sort', 'has_attachment'] as const

const base = '/api/v1/mail'
const accountPath = (id: AccountId): string => `${base}/accounts/${String(id)}`
const messagePath = (ref: string): string => `${base}/messages/${encodeURIComponent(ref)}`

function withQuery(path: string, query: Record<string, string | number | boolean | undefined>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) {
      params.set(key, String(value))
    }
  }
  const text = params.toString()
  return text === '' ? path : `${path}?${text}`
}

/** Every folder of the account, the levels that hold only folders too (`selectable: false`). */
export async function listMailFolders(accountId: AccountId, signal?: AbortSignal): Promise<MailFolder[]> {
  const list = await apiGetJson(`${accountPath(accountId)}/folders?all=true`, {
    init: { signal },
    schema: folderListSchema,
  })
  return list.folders
}

const folderCreatedSchema = z.object({ account_id: accountIdSchema, path: z.string() })

const folderRenamedSchema = z.object({
  account_id: accountIdSchema,
  /** The old path. */
  folder: z.string(),
  path: z.string(),
  /** The rule sets and filters pointed at the new path. */
  rule_sets: z.array(z.number()),
  filters: z.array(z.number()),
  /** `false`: the folder was renamed but its rule sets still name the old path. */
  rule_sets_updated: z.boolean(),
  /** The rule sets for all accounts naming the old path, which a rename leaves as they are. */
  all_accounts_rule_sets: z.array(z.number()),
})
export type FolderRenamed = z.infer<typeof folderRenamedSchema>

const folderDeletedSchema = z.object({ account_id: accountIdSchema, folder: z.string(), messages: z.number() })

/** Creates `name` under `parent` (`null`: the top level); the new folder's path. */
export async function createFolder(accountId: AccountId, parent: string | null, name: string): Promise<string> {
  const created = await apiPostJson(`${accountPath(accountId)}/folders`, { parent, name }, { schema: folderCreatedSchema })
  return created.path
}

/** Renames `folder` to `name` under `parent`, its subfolders and mail with it, and its rule sets. */
export function renameFolder(accountId: AccountId, folder: string, parent: string | null, name: string): Promise<FolderRenamed> {
  return apiPostJson(`${accountPath(accountId)}/folders/rename`, { folder, parent, name }, { schema: folderRenamedSchema })
}

/**
 * Deletes `folder` and its mail for good. `messages` is the count the user confirmed: a different
 * count now is refused as `folder_changed`, carrying it.
 */
export async function deleteFolder(accountId: AccountId, folder: string, messages: number): Promise<void> {
  await apiPostJson(`${accountPath(accountId)}/folders/delete`, { folder, messages }, { schema: folderDeletedSchema })
}

const folderMarkedReadSchema = z.object({ account_id: accountIdSchema, folder: z.string(), marked: z.number() })

/**
 * Marks every unread message of `folder` read, up to `uidNext`: mail that arrived after the listing
 * the user confirmed from stays unread. A changed `uidValidity` is refused (`stale_reference`).
 * Both `null` for a folder whose listing gave none (a Gmail label's counted from the index): the
 * server reads them then, and refuses any other folder. The count marked.
 */
export async function markFolderRead(accountId: AccountId, folder: string, uidValidity: number | null, uidNext: number | null): Promise<number> {
  const answer = await apiPostJson(
    `${accountPath(accountId)}/folders/mark-read`,
    { folder, uid_validity: uidValidity, uid_next: uidNext },
    { schema: folderMarkedReadSchema },
  )
  return answer.marked
}

/** A folder's messages; with `subfolders`, its subfolders' too, merged newest first. */
export function listMessages(
  accountId: AccountId,
  options: { folder: string; subfolders?: boolean; cursor?: string | null; limit?: number; grouped?: boolean },
  signal?: AbortSignal,
): Promise<MessagePage> {
  const path = withQuery(`${accountPath(accountId)}/messages`, {
    folder: options.folder,
    subfolders: options.subfolders === true ? true : undefined,
    limit: options.limit,
    cursor: options.cursor ?? undefined,
    group: options.grouped === true ? 'thread' : undefined,
  })
  return apiGetJson(path, { init: { signal }, schema: messagePageSchema })
}

/** With `subfolders`, the query's one folder is searched with its subfolders. */
export function searchMessages(
  query: SearchQuery,
  options: { subfolders?: boolean; cursor?: string | null; limit?: number; grouped?: boolean } = {},
  signal?: AbortSignal,
): Promise<MessagePage> {
  const body = {
    ...matchBody({ query, subfolders: options.subfolders === true }),
    limit: options.limit,
    cursor: options.cursor ?? undefined,
    group: options.grouped === true ? 'thread' : undefined,
  }
  return apiPostJson(`${base}/search`, body, { init: { signal }, schema: messagePageSchema })
}

/** What a list or a search shows, to act on all it matches: a folder's list is a search with no criteria. */
export interface MatchScope {
  query: SearchQuery
  subfolders: boolean
}

/**
 * A search's scope and criteria as a body, not a query string: search terms stay out of access
 * logs. The URL keeps the account as text; the API takes its number, or `*`.
 */
export function matchBody({ query, subfolders }: MatchScope): object {
  const { account, ...terms } = query
  return {
    ...terms,
    account: account === undefined || account === '*' ? account : Number(account),
    subfolders: subfolders ? true : undefined,
  }
}

const viewSourceSchema = z.object({ account_id: accountIdSchema, folder: z.string() })

/** A saved view: folders of any of the user's accounts (`sources`), or a search line (`query`). */
const savedViewSchema = z.object({
  id: z.number(),
  name: z.string(),
  sources: z.array(viewSourceSchema).nullable(),
  query: z.string().nullable(),
  position: z.number(),
})
export type SavedView = z.infer<typeof savedViewSchema>

const viewsSchema = z.object({ views: z.array(savedViewSchema) })

export const ALL_INBOXES = 'all_inboxes'
/** A saved view's id, or the built-in *All inboxes*. */
export type ViewKey = number | typeof ALL_INBOXES

const viewPageSchema = pageFields
  .extend({
    /** The view's folders left out, off the index or not ready; `*` for an account's every folder. */
    not_indexed: z.array(viewSourceSchema),
  })
  .transform(withConversations)
export type ViewPage = Omit<z.infer<typeof viewPageSchema>, 'conversations'> & { conversations?: Conversation[] | null }

const viewsPath = `${base}/views`

/** The user's saved views, in their order; *All inboxes* is not among them. */
export async function listViews(signal?: AbortSignal): Promise<SavedView[]> {
  const answer = await apiGetJson(viewsPath, { init: { signal }, schema: viewsSchema })
  return answer.views
}

/** Saves a search line (in the search syntax) as a view, last. */
export function createView(name: string, query: string): Promise<SavedView> {
  return apiPostJson(viewsPath, { name, query }, { schema: savedViewSchema })
}

export function renameView(id: number, name: string): Promise<SavedView> {
  return apiPatchJson(`${viewsPath}/${String(id)}`, { name }, { schema: savedViewSchema })
}

export async function deleteView(id: number): Promise<void> {
  await apiDelete(`${viewsPath}/${String(id)}`)
}

/** Puts the views in the order of `ids`, every one of them once; the views as ordered. */
export async function reorderViews(ids: number[]): Promise<SavedView[]> {
  const answer = await apiPutJson(`${viewsPath}/order`, { view_ids: ids }, { schema: viewsSchema })
  return answer.views
}

/** A page of the view's messages, newest first, read from the index only; `grouped`, by conversation. */
export function viewMessages(view: ViewKey, cursor: string | null, signal?: AbortSignal, grouped = false): Promise<ViewPage> {
  const path = withQuery(`${viewsPath}/${String(view)}/messages`, {
    cursor: cursor ?? undefined,
    group: grouped ? 'thread' : undefined,
  })
  return apiGetJson(path, { init: { signal }, schema: viewPageSchema })
}

/** The conversation of the message `ref` names, newest first, read live: its folder and Sent (on Gmail, its thread). */
export function readThread(ref: string, signal?: AbortSignal): Promise<MessagePage> {
  return apiGetJson(`${messagePath(ref)}/thread`, { init: { signal }, schema: messagePageSchema })
}

/** One window of the message's text from `offset`; reading never marks it seen. */
export function readMessage(
  ref: string,
  options: { offset?: number; includeQuoted?: boolean } = {},
  signal?: AbortSignal,
): Promise<MessageView> {
  const path = withQuery(messagePath(ref), {
    offset: options.offset,
    include_quoted: options.includeQuoted === true ? true : undefined,
  })
  return apiGetJson(path, { init: { signal }, schema: messageViewSchema })
}

/**
 * The standalone, sanitised HTML document of a message, for a sandboxed iframe's `src`; `dark`
 * adapts it to a dark theme (dark colours, or inverted with its images kept). The dark document
 * also holds the message's own colours, shown when its fragment is `#mailroom-original`
 * (`original`), else `#mailroom-dark`: a frame switches between them with the fragment alone, a
 * same-document navigation with no request, no reload and no script. Never no fragment at all,
 * which would load the document again.
 */
export function messageHtmlUrl(ref: string, remote: boolean, dark = false, original = false): string {
  const query = [...(remote ? ['remote=1'] : []), ...(dark ? ['dark=1'] : [])].join('&')
  const fragment = dark ? (original ? '#mailroom-original' : '#mailroom-dark') : ''
  return `${getApiBaseUrl()}${messagePath(ref)}/html${query === '' ? '' : `?${query}`}${fragment}`
}

/**
 * Asks whether the HTML document would be served (`/html/check`, 204, which keeps the server's
 * view for the document) before a frame shows it: a refusal then throws, as any API call's, for
 * the client to word, instead of showing up inside the frame as the API's JSON.
 */
export async function checkMessageHtml(ref: string, remote: boolean, signal?: AbortSignal): Promise<void> {
  await apiGetJson(withQuery(`${messagePath(ref)}/html/check`, { remote: remote ? 1 : undefined }), {
    init: { signal },
  })
}

const rememberedSenderSchema = z.object({ address: z.string(), created_at: z.string() })
export type RememberedSender = z.infer<typeof rememberedSenderSchema>

/** The senders whose remote images the user shows without asking, by address. */
export async function listRemoteSenders(signal?: AbortSignal): Promise<RememberedSender[]> {
  const answer = await apiGetJson(`${base}/remote-senders`, {
    init: { signal },
    schema: z.object({ senders: z.array(rememberedSenderSchema) }),
  })
  return answer.senders
}

/**
 * Remembers the From of the message `ref` names: the server reads it and refuses
 * (`sender_not_verified`) unless DMARC passed for that one From.
 */
export function addRemoteSender(ref: string): Promise<RememberedSender> {
  return apiPostJson(`${base}/remote-senders`, { ref }, { schema: rememberedSenderSchema })
}

export async function removeRemoteSender(address: string): Promise<void> {
  await apiPostJson(`${base}/remote-senders/remove`, { address })
}

/** The senders whose mail the user reads as text, by address. */
export async function listTextSenders(signal?: AbortSignal): Promise<RememberedSender[]> {
  const answer = await apiGetJson(`${base}/sender-views`, {
    init: { signal },
    schema: z.object({ senders: z.array(rememberedSenderSchema) }),
  })
  return answer.senders
}

/** The view the From of the message `ref` names opens in, read by the server: `text` remembers it, `html` forgets it. */
export async function chooseSenderView(ref: string, view: 'html' | 'text'): Promise<void> {
  await apiPostJson(`${base}/sender-views`, { ref, view })
}

export async function removeTextSender(address: string): Promise<void> {
  await apiPostJson(`${base}/sender-views/remove`, { address })
}

const suggestedAddressSchema = addressSchema.extend({ sent: z.boolean() })
export type SuggestedAddress = z.infer<typeof suggestedAddressSchema>

/**
 * Up to ten addresses starting with `q`, from the mail index: those the user sent to (`sent`)
 * first, then people who wrote to them directly. What is typed goes in the body, never in a URL.
 */
export async function suggestAddresses(q: string, signal?: AbortSignal): Promise<SuggestedAddress[]> {
  const answer = await apiPostJson(
    `${base}/addresses`,
    { q },
    { init: { signal }, schema: z.object({ addresses: z.array(suggestedAddressSchema) }) },
  )
  return answer.addresses
}

const mutedThreadSchema = z.object({
  id: z.number(),
  account_id: accountIdSchema,
  /** How many Message-IDs it is known by. */
  ids: z.number(),
  /** Known by its Gmail thread too. */
  gmail: z.boolean(),
  /** The account's Run filters: off, muting has no effect until it is on. */
  run_filters: z.boolean(),
  created_at: z.string(),
})
export type MutedThread = z.infer<typeof mutedThreadSchema>

/** Mutes the conversation of the message `ref` names: its new messages in INBOX arrive marked read. */
export function muteThread(ref: string): Promise<MutedThread> {
  return apiPostJson(`${messagePath(ref)}/mute`, undefined, { schema: mutedThreadSchema })
}

/** The user's muted threads, oldest first. */
export async function listMutedThreads(signal?: AbortSignal): Promise<MutedThread[]> {
  const answer = await apiGetJson(`${base}/muted`, {
    init: { signal },
    schema: z.object({ threads: z.array(mutedThreadSchema) }),
  })
  return answer.threads
}

export async function unmuteThread(id: number): Promise<void> {
  await apiDelete(`${base}/muted/${String(id)}`)
}

const snoozeSchema = z.object({
  snooze_id: z.string(),
  account: accountIdSchema,
  subject: z.string(),
  /** Its From address. */
  sender: z.string().nullable(),
  /** The folder it goes back to. */
  folder: z.string(),
  until: z.string(),
  /** Why its last wake failed; it is tried again later. */
  code: z.string().nullable(),
})

export type Snooze = z.infer<typeof snoozeSchema>

/**
 * Snoozes the message `ref` names until `until`: it moves to its account's Snoozed folder, and the
 * server moves it back, unread, then. `sourceFolder`: the folder it was listed under, as a move's.
 */
export function snoozeMessage(ref: string, until: Date, sourceFolder?: string | null): Promise<Snooze> {
  const body = { until: until.toISOString(), ...(sourceFolder == null ? {} : { source_folder: sourceFolder }) }
  return apiPostJson(`${messagePath(ref)}/snooze`, body, { schema: snoozeSchema })
}

/** The user's snoozed messages, soonest back first. */
export async function listSnoozes(signal?: AbortSignal): Promise<Snooze[]> {
  const answer = await apiGetJson(`${base}/snoozes`, { init: { signal }, schema: z.object({ snoozes: z.array(snoozeSchema) }) })
  return answer.snoozes
}

/** Brings a snoozed message back now: the server moves it in a moment. */
export function wakeSnooze(snoozeId: string): Promise<Snooze> {
  return apiPostJson(`${base}/snoozes/${encodeURIComponent(snoozeId)}/wake`, undefined, { schema: snoozeSchema })
}

/** The longest age limit of the mail index, the user's or the instance's (`INDEX_MAX_AGE_DAYS`). */
export const INDEX_MAX_AGE_DAYS = 36500

const indexFolderSchema = z.object({
  path: z.string(),
  /** The server's hierarchy delimiter for the path; `null` when it gives none. */
  delimiter: z.string().nullable(),
  role: z.enum(FOLDER_ROLES).nullable(),
  /** `folder`: indexed on its own; `label`: a Gmail folder read from All Mail, whose switch and sync it shows; `live`: never indexed. */
  indexed_as: z.enum(['folder', 'label', 'live']),
  enabled: z.boolean(),
  /** The server's count; `null` when its STATUS failed. */
  total: z.number().nullable(),
  /** This and the rest `null` while the folder is off the index. */
  state: z.enum(['pending', 'syncing', 'ready', 'failed']).nullable(),
  /** The mail code that failed the last sync. */
  error: z.string().nullable(),
  /** What the index holds; `null` for a label. */
  indexed: z.number().nullable(),
  unread: z.number().nullable(),
  /** The age limit's day the last sync kept to; `null`: every message. */
  oldest_indexed: z.string().nullable(),
  last_synced_at: z.string().nullable(),
})
export type IndexFolder = z.infer<typeof indexFolderSchema>

/** The live updates stream's events (`GET /mail/events`); mail-derived text in them is raw, never HTML. */
const liveEventSchemas = {
  folder_changed: z.object({ account: accountIdSchema, folder: z.string(), unread: z.number(), total: z.number() }),
  sync: z.object({
    account: accountIdSchema,
    folder: z.string(),
    state: z.enum(['pending', 'syncing', 'ready', 'failed']),
    progress: z.number(),
  }),
  new_mail: z.object({
    account: accountIdSchema,
    folder: z.string(),
    count: z.number(),
    messages: z.array(z.object({ ref: z.string(), sender: z.string(), subject: z.string() })),
  }),
}
export type LiveEventType = keyof typeof liveEventSchemas
export const LIVE_EVENT_TYPES = Object.keys(liveEventSchemas) as LiveEventType[]
export type LiveEvent = {
  [K in LiveEventType]: { type: K } & z.infer<(typeof liveEventSchemas)[K]>
}[LiveEventType]

/** An event's data as the stream sent it; `null` when it is not what the type says. */
export function parseLiveEvent(type: LiveEventType, data: string): LiveEvent | null {
  let body: unknown
  try {
    body = JSON.parse(data)
  } catch {
    return null
  }
  const parsed = liveEventSchemas[type].safeParse(body)
  return parsed.success ? ({ type, ...parsed.data } as LiveEvent) : null
}

/** The signed-in user's live updates stream; `viewing` names the folder the browser shows. */
export function mailEventsUrl(viewing: { account: AccountId; folder: string } | null): string {
  const query = viewing === null ? {} : { account: viewing.account, folder: viewing.folder }
  return `${getApiBaseUrl()}${withQuery(`${base}/events`, query)}`
}

const accountNotificationsSchema = z.object({ account_id: accountIdSchema, folders: z.array(z.string()) })
export type AccountNotifications = z.infer<typeof accountNotificationsSchema>
const notificationsSchema = z.object({ accounts: z.array(accountNotificationsSchema) })

/** Each account's folders whose new mail shows as a desktop notification. */
export async function readNotificationFolders(signal?: AbortSignal): Promise<AccountNotifications[]> {
  const read = await apiGetJson(`${base}/notifications`, { init: { signal }, schema: notificationsSchema })
  return read.accounts
}

/** The account's folders whose new mail shows as a desktop notification, all at once. */
export function setNotificationFolders(accountId: AccountId, folders: string[]): Promise<AccountNotifications> {
  return apiPutJson(`${base}/notifications/accounts/${String(accountId)}`, { folders }, { schema: accountNotificationsSchema })
}

const indexStatusSchema = z.object({
  account_id: accountIdSchema,
  gmail: z.boolean(),
  /** The age limit in force: the user's, capped by the instance's; `null`: none. */
  max_age_days: z.number().nullable(),
  /** The instance's cap; `null` when it has none. */
  max_age_cap_days: z.number().nullable(),
  folders: z.array(indexFolderSchema),
})
export type IndexStatus = z.infer<typeof indexStatusSchema>

/**
 * The account's selectable folders, each with whether and how the mail index keeps it. `waiting`:
 * the server's count of the folders waiting for or in their sync only (the others' `total` null),
 * so that a page following a first sync asks the server little.
 */
export function readIndexStatus(accountId: AccountId, signal?: AbortSignal, waiting = false): Promise<IndexStatus> {
  const query = waiting ? '?totals=waiting' : ''
  return apiGetJson(`${base}/index/accounts/${String(accountId)}${query}`, { init: { signal }, schema: indexStatusSchema })
}

/** Switches a folder on or off the index; off forgets what the index kept of it. */
export async function setIndexFolder(accountId: AccountId, folder: string, enabled: boolean): Promise<void> {
  await apiPutJson(`${base}/index/accounts/${String(accountId)}/folders`, { folder, enabled })
}

const unsubscribedSchema = z.object({
  outcome: z.enum(['ok', 'rejected', 'refused', 'failed']),
  status: z.number().nullable(),
  list_id: z.string().nullable(),
})
export type Unsubscribed = z.infer<typeof unsubscribedSchema>

/** Unsubscribes in one click (RFC 8058) from the list the message `ref` names came from; the server checks it first. */
export function unsubscribe(ref: string): Promise<Unsubscribed> {
  return apiPostJson(`${messagePath(ref)}/unsubscribe`, undefined, { schema: unsubscribedSchema })
}

/** A download of one attachment (or the calendar part). */
export function messagePartUrl(ref: string, part: string): string {
  return `${getApiBaseUrl()}${messagePath(ref)}/parts/${encodeURIComponent(part)}`
}

/** A download of the whole message as stored, a `.eml` file named after its subject. */
export function messageSourceUrl(ref: string): string {
  return `${getApiBaseUrl()}${messagePath(ref)}/source`
}

/** The message as a page of its own for the browser's print: it runs nothing and loads nothing remote. */
export function messagePrintUrl(ref: string): string {
  return `${getApiBaseUrl()}${messagePath(ref)}/print`
}

const organizeGroupSchema = z.object({
  account: accountIdSchema,
  folder: z.string(),
  ok: z.boolean(),
  count: z.number(),
  new_refs: z.array(z.string()).nullable(),
  /** On a move to another account, that account: `new_refs` are its. */
  destination_account: accountIdSchema.nullable().default(null),
  junk: z.enum(['spam', 'ham']).nullable(),
  flags: z.array(
    z.object({
      ref: z.string(),
      seen: z.boolean().nullable(),
      flagged: z.boolean().nullable(),
      to_sort: z.boolean().nullable(),
    }),
  ),
  error: z.object({ code: z.string(), message: z.string() }).nullable(),
})
/** One account and folder of a batch: groups succeed or fail on their own. */
export type OrganizeGroup = z.infer<typeof organizeGroupSchema>

const organizeAnswerSchema = z.object({ results: z.array(organizeGroupSchema) })

/** The flags a batch sets; at least one. */
export interface FlagChange {
  seen?: boolean
  flagged?: boolean
  /** Refused as `unsupported` where the account's server keeps no keywords. */
  to_sort?: boolean
}

async function organize(route: string, body: object, signal?: AbortSignal): Promise<OrganizeGroup[]> {
  const answer = await apiPostJson(`${base}/messages/${route}`, body, { init: { signal }, schema: organizeAnswerSchema })
  return answer.results
}

/**
 * Where a Gmail All Mail ref is acted on, so it leaves that view only: `folder`, the folder shown,
 * and for some refs their own (`each`, a row's `listed_folder`), over it.
 */
export interface SourceFolders {
  folder?: string | undefined
  each?: Readonly<Record<string, string>>
}

function sourceFields(source: SourceFolders | undefined): object {
  const each = source?.each ?? {}
  return { source_folder: source?.folder, source_folders: Object.keys(each).length > 0 ? each : undefined }
}

/**
 * Up to 50 refs, from any folders; a group's failure is in its result, and only a batch where no
 * group succeeded throws (the first failure's refusal). `source`, here and on archive, junk and
 * trash: where a Gmail All Mail ref is acted on (`SourceFolders`).
 */
export function moveMessages(
  refs: string[],
  folder: string,
  signal?: AbortSignal,
  source?: SourceFolders,
): Promise<OrganizeGroup[]> {
  return organize('move', { refs, folder, ...sourceFields(source) }, signal)
}

/** What becomes of the originals of a move to another account: their account's Trash, or deleted for good. */
export type MoveRemoval = 'trash' | 'delete'

/**
 * Moves them to `folder` of `account`, another of the user's accounts: copied there, then the
 * originals removed as `removal` says. A group whose copies were made but whose originals could
 * not be removed fails with its `new_refs`: those messages are in both accounts.
 */
export function moveToAccount(
  refs: string[],
  account: AccountId,
  folder: string,
  removal: MoveRemoval,
  signal?: AbortSignal,
): Promise<OrganizeGroup[]> {
  return organize('move-to-account', { refs, account, folder, removal }, signal)
}

export function archiveMessages(refs: string[], signal?: AbortSignal, source?: SourceFolders): Promise<OrganizeGroup[]> {
  return organize('archive', { refs, ...sourceFields(source) }, signal)
}

/** Reports them as spam: moved to the account's Junk folder, which teaches its spam filter. */
export function junkMessages(refs: string[], signal?: AbortSignal, source?: SourceFolders): Promise<OrganizeGroup[]> {
  return organize('junk', { refs, ...sourceFields(source) }, signal)
}

/** Moves them to Trash, never deleting them. */
export function trashMessages(refs: string[], signal?: AbortSignal, source?: SourceFolders): Promise<OrganizeGroup[]> {
  return organize('trash', { refs, ...sourceFields(source) }, signal)
}

export function flagMessages(refs: string[], change: FlagChange, signal?: AbortSignal): Promise<OrganizeGroup[]> {
  return organize('flag', { refs, ...change }, signal)
}

/** The most messages a bulk action acts on, all calls together: past it, `too_many`. */
export const BULK_MAX = 5000

/** What a bulk action does to every message a scope matches. */
export type BulkAction = ({ kind: 'flag' } & FlagChange) | { kind: 'move'; folder: string } | { kind: 'trash' }

const matchCountSchema = z.object({ total: z.number(), failures: z.array(failureSchema) })

const bulkAnswerSchema = z.object({
  done: z.number(),
  /** The next call's `expected`: 0 once every message is done. */
  remaining: z.number(),
  results: z.array(organizeGroupSchema),
  failures: z.array(failureSchema),
})
export type BulkAnswer = z.infer<typeof bulkAnswerSchema>

/** How many messages `scope` matches; with `action`, how many it would still change. */
export function countMatching(
  scope: MatchScope,
  action: BulkAction | null,
  signal?: AbortSignal,
): Promise<z.infer<typeof matchCountSchema>> {
  return apiPostJson(`${base}/messages/count`, { ...matchBody(scope), action: action ?? undefined }, {
    init: { signal },
    schema: matchCountSchema,
  })
}

/**
 * `action` on at most 500 of what `scope` matches, refused as `count_changed` (carrying
 * `matching`, the count now) unless `expected` still matches, and as `too_many` past `BULK_MAX`.
 * Called again with each answer's `remaining` until it is 0.
 */
export function actOnMatching(scope: MatchScope, action: BulkAction, expected: number, signal?: AbortSignal): Promise<BulkAnswer> {
  return apiPostJson(`${base}/messages/bulk`, { ...matchBody(scope), action, expected }, {
    init: { signal },
    schema: bulkAnswerSchema,
  })
}

const keepPartSchema = partSchema.extend({ ref: z.string() })
/** A part of another message (or of the draft itself) a draft keeps. */
export type KeptPart = z.infer<typeof keepPartSchema>

const shownPartSchema = partSchema.extend({ content_id: z.string() })
/** An inline part: what the HTML shows as `cid:<content_id>`. */
export type ShownPart = z.infer<typeof shownPartSchema>
const keptShownPartSchema = shownPartSchema.extend({ ref: z.string() })
export type KeptShownPart = z.infer<typeof keptShownPartSchema>

const composeFieldsSchema = z.object({
  account: accountIdSchema,
  from: z.string().nullable(),
  to: z.array(addressSchema),
  cc: z.array(addressSchema),
  bcc: z.array(addressSchema),
  subject: z.string(),
  text: z.string(),
  /** The body as HTML, when it has one: its text is then derived from it. */
  html: z.string().nullable(),
  answers: z.string().nullable(),
  forwards: z.string().nullable(),
})

const prefillSchema = composeFieldsSchema.extend({ attachments: z.array(keepPartSchema), inline: z.array(keptShownPartSchema) })
export type Prefill = z.infer<typeof prefillSchema>
export type PrefillMode = 'reply' | 'reply_all' | 'forward' | 'unsubscribe'

export const SEND_STATES = ['sending', 'sent', 'failed', 'unknown'] as const

const draftSendSchema = z.object({ send_id: z.string(), state: z.enum(SEND_STATES), code: z.string().nullable() })
export type DraftSend = z.infer<typeof draftSendSchema>

/** A draft held for the worker to send at `send_at`: `held` until it takes it, `failed` with `code` when that ended before a delivery. */
const scheduledSchema = z.object({
  scheduled_id: z.string(),
  account: accountIdSchema,
  ref: z.string(),
  subject: z.string(),
  /** The draft's addresses (To, Cc, Bcc) as it was scheduled. */
  recipients: z.array(z.string()),
  send_at: z.string(),
  /** How long until `send_at` as the server answered, by its clock. */
  remaining_ms: z.number().int().nonnegative(),
  state: z.enum(['held', 'sending', 'failed']),
  code: z.string().nullable(),
  /** Who scheduled it: the user, or an agent through MCP. */
  by: z.enum(['user', 'agent']),
  /** An agent's connected app: its name, or its client id when it gave none. */
  app: z.string().nullable(),
})
export type ScheduledSend = z.infer<typeof scheduledSchema>

const draftSchema = composeFieldsSchema.extend({
  ref: z.string(),
  compose_id: z.string().nullable(),
  lossy: z.boolean(),
  /** Imported as built: its body is sent as stored, and only `keepDesigned` saves it again. */
  designed: z.boolean(),
  parts: z.array(partSchema),
  inline: z.array(shownPartSchema),
  send: draftSendSchema.nullable(),
  /** Its held send still to go (send later, or an Undo's), which the user's first change cancels. */
  scheduled: scheduledSchema.nullable(),
})
export type Draft = z.infer<typeof draftSchema>

const heldRefusalSchema = z.object({ detail: z.object({ code: z.literal('draft_not_editable'), send: draftSendSchema }) })

/** The send that made the server refuse a save, which it names as the draft may be gone. */
export function holdingSend(error: unknown): DraftSend | null {
  const body = error instanceof ApiRequestError ? heldRefusalSchema.safeParse(error.body) : null
  return body?.success === true ? body.data.detail.send : null
}

const savedDraftSchema = z.object({
  ref: z.string().nullable(),
  parts: z.array(partSchema),
  inline: z.array(shownPartSchema),
  replaced: z.boolean(),
  autosave: z.boolean(),
})
export type SavedDraft = z.infer<typeof savedDraftSchema>

const findingSchema = z.object({
  code: z.string(),
  /** The server's English, for a code this client has no words for. */
  text: z.string(),
  count: z.number().int(),
  /** Content-IDs or file names from the message itself, at most 20. */
  items: z.array(z.string()),
})
/** Something to look at before a designed message is sent. */
export type Finding = z.infer<typeof findingSchema>

const importedDraftSchema = savedDraftSchema.extend({ findings: z.array(findingSchema) })
export type ImportedDraft = z.infer<typeof importedDraftSchema>

/** An import's fields: what is left `null` the .eml's, `from` the default identity. */
export interface ImportFields {
  account: AccountId
  compose_id: string
  /** The version an import replaces. */
  ref: string | null
  from: string | null
  to: MailAddress[] | null
  cc: MailAddress[] | null
  bcc: MailAddress[] | null
  subject: string | null
}

/** A message built elsewhere: an .eml, or an HTML file with its images and attachments. */
export type ImportSource = { kind: 'eml'; eml: File } | { kind: 'html'; html: File; files: File[] }

/** Saves a message built elsewhere as a draft, as it was built, with the checks of it. */
export async function importDraft(fields: ImportFields, source: ImportSource): Promise<ImportedDraft> {
  const form = new FormData()
  form.set('draft', JSON.stringify(fields))
  if (source.kind === 'eml') {
    form.set('eml', source.eml)
  } else {
    form.set('html', await source.html.text())
    for (const file of source.files) {
      form.append('files[]', file)
    }
  }
  return apiPostForm(withQuery(`${base}/drafts`, { import: source.kind }), form, { schema: importedDraftSchema })
}

/** Saves the designed draft `fields.ref` again as it was built, under the fields given. */
export function keepDesigned(fields: ImportFields & { ref: string }, options: { keepalive?: boolean } = {}): Promise<ImportedDraft> {
  const form = new FormData()
  form.set('draft', JSON.stringify(fields))
  return apiPostForm(withQuery(`${base}/drafts`, { import: 'kept' }), form, {
    init: { keepalive: options.keepalive === true },
    schema: importedDraftSchema,
  })
}

const foundDraftSchema = z.object({ ref: z.string(), parts: z.array(partSchema), inline: z.array(shownPartSchema) })
export type FoundDraft = z.infer<typeof foundDraftSchema>

const sendResultSchema = z.object({
  send_id: z.string(),
  state: z.enum(SEND_STATES),
  code: z.string().nullable(),
  filed: z.boolean().nullable(),
  rejected: z.array(z.string()),
  notes: z.array(z.string()),
  note_codes: z.array(z.string()),
  settled: z.boolean(),
})
export type SendResult = z.infer<typeof sendResultSchema>

/** A save's fields: `html` goes as a part of its own, the rest as the `draft` part. */
export interface DraftFields {
  account: AccountId
  compose_id: string
  ref: string | null
  answers: string | null
  forwards: string | null
  from: string | null
  to: MailAddress[]
  cc: MailAddress[]
  bcc: MailAddress[]
  subject: string
  /** Not used when there is `html`. */
  text: string
  html: string | null
  /** The Content-ID of each upload the HTML shows in place, in order; the files after them are attachments. */
  content_ids: string[]
  keep: { ref: string; part: string; inline?: boolean }[]
}

const draftPath = (ref: string): string => `${base}/drafts/${encodeURIComponent(ref)}`

/** The server's cap on a reply's `quote`. */
export const MAX_QUOTE_CHARS = 20_000

/** The fields a reply or forward of `ref` starts from; writes nothing. A reply with a `quote` quotes only that. */
export function prefillDraft(ref: string, mode: PrefillMode, quote: string | null, signal?: AbortSignal): Promise<Prefill> {
  const body = quote === null ? { ref, mode } : { ref, mode, quote }
  return apiPostJson(`${base}/drafts/prefill`, body, { init: { signal }, schema: prefillSchema })
}

/**
 * The text an HTML body is sent with (the server's conversion), for a switch to plain text. It
 * goes to the save's route, the one the host's proxy lets an HTML body this size through to.
 */
export async function draftText(html: string, signal?: AbortSignal): Promise<string> {
  const form = new FormData()
  form.set('html', html)
  const answer = await apiPostForm(withQuery(`${base}/drafts`, { convert: 'text' }), form, {
    init: { signal },
    schema: z.object({ text: z.string() }),
  })
  return answer.text
}

/**
 * Replaces the draft `fields.ref` (or writes the first version). `keepalive` outlives the page, so
 * it must fit the browser's small keepalive quota: text only, no `files`. `files` start with the
ones `fields.content_ids` name.
 */
export function saveDraft(fields: DraftFields, files: File[], options: { keepalive?: boolean } = {}): Promise<SavedDraft> {
  const { html, ...rest } = fields
  const form = new FormData()
  form.set('draft', JSON.stringify(rest))
  // Raw, not in the JSON: escaping would let the part outgrow the HTML cap, which counts UTF-8.
  if (html !== null) {
    form.set('html', html)
  }
  for (const file of files) {
    form.append('files[]', file)
  }
  return apiPostForm(`${base}/drafts`, form, { init: { keepalive: options.keepalive === true }, schema: savedDraftSchema })
}

export function readDraft(ref: string, signal?: AbortSignal): Promise<Draft> {
  return apiGetJson(draftPath(ref), { init: { signal }, schema: draftSchema })
}

/** The newest version saved under `composeId`, or `null` when there is none. */
export async function findDraft(account: AccountId, composeId: string, signal?: AbortSignal): Promise<FoundDraft | null> {
  const path = withQuery(`${base}/drafts/find`, { account, compose_id: composeId })
  try {
    return await apiGetJson(path, { init: { signal }, schema: foundDraftSchema })
  } catch (error) {
    if (error instanceof ApiRequestError && error.code === 'not_found') {
      return null
    }
    throw error
  }
}

export async function deleteDraft(ref: string): Promise<void> {
  await apiDeleteJson(draftPath(ref), { schema: z.object({ removed: z.enum(['expunged', 'trashed']) }) })
}

/** Starts the send (an id to follow), or the result when this draft's key is already sent. */
/**
 * `undoable`: held for the user's Undo delay first, answered with the held send, unless a send of
 * the draft began already.
 */
export function sendDraft(
  ref: string,
  options: { confirmResend?: boolean; undoable?: boolean } = {},
): Promise<SendResult | { send_id: string } | ScheduledSend> {
  const body = {
    ...(options.confirmResend === true ? { confirm_resend: true } : {}),
    ...(options.undoable === true ? { undoable: true } : {}),
  }
  return apiPostJson(`${draftPath(ref)}/send`, body, {
    schema: z.union([sendResultSchema, scheduledSchema, z.object({ send_id: z.string() })]),
  })
}

export function readSend(sendId: string, signal?: AbortSignal): Promise<SendResult> {
  return apiGetJson(`${base}/sends/${encodeURIComponent(sendId)}`, { init: { signal }, schema: sendResultSchema })
}

/** The send of the draft saved under `composeId`, there or not by now: a held send's, once it went. */
export function readComposeSend(account: AccountId, composeId: string, signal?: AbortSignal): Promise<SendResult> {
  const query = new URLSearchParams({ account: String(account), compose_id: composeId })
  return apiGetJson(`${base}/sends?${query.toString()}`, { init: { signal }, schema: sendResultSchema })
}

/** The checks of the draft `ref` as stored, before it is sent. */
export async function draftFindings(ref: string, signal?: AbortSignal): Promise<Finding[]> {
  const answer = await apiGetJson(`${draftPath(ref)}/findings`, { init: { signal }, schema: z.object({ findings: z.array(findingSchema) }) })
  return answer.findings
}

/**
 * Sends the draft `ref` as a test, to the account's default identity (`to`); the draft stays as it
 * is. `testId` is one per click: the same one again answers the send it made.
 */
export function sendTest(ref: string, testId: string): Promise<{ send_id: string; to: string }> {
  return apiPostJson(`${draftPath(ref)}/test`, { test_id: testId }, { schema: z.object({ send_id: z.string(), to: z.string() }) })
}

/** Send later: the draft `ref` held for the worker to send at `sendAt`, which must still be to come. */
export function scheduleDraft(ref: string, sendAt: Date): Promise<ScheduledSend> {
  return apiPostJson(`${draftPath(ref)}/schedule`, { send_at: sendAt.toISOString() }, { schema: scheduledSchema })
}

/** The user's held sends, soonest first, failed ones included until cancelled. */
export async function listScheduled(signal?: AbortSignal): Promise<ScheduledSend[]> {
  const list = await apiGetJson(`${base}/scheduled`, {
    init: { signal },
    schema: z.object({ scheduled: z.array(scheduledSchema) }),
  })
  return list.scheduled
}

/** A held send; `not_found` once it went or was cancelled. */
export function readScheduled(scheduledId: string, signal?: AbortSignal): Promise<ScheduledSend> {
  return apiGetJson(`${base}/scheduled/${encodeURIComponent(scheduledId)}`, { init: { signal }, schema: scheduledSchema })
}

/** Cancels a held send, the draft left in Drafts; `send_started` once the worker took it. */
export function cancelScheduled(scheduledId: string): Promise<ScheduledSend> {
  return apiDeleteJson(`${base}/scheduled/${encodeURIComponent(scheduledId)}`, { schema: scheduledSchema })
}

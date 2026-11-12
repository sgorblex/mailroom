import { z } from 'zod'

import { getApiBaseUrl } from './api-base-url.ts'
import { ApiRequestError, apiDeleteJson, apiGetJson, apiPostForm, apiPostJson, reportSessionEnded } from './http.ts'

/** The REST contract of `docs/requirements/mail-client.md`. Refs and cursors are opaque strings. */

export const FOLDER_ROLES = ['inbox', 'sent', 'drafts', 'trash', 'junk', 'archive', 'all', 'flagged'] as const
export type FolderRole = (typeof FOLDER_ROLES)[number]

const addressSchema = z.object({ name: z.string(), address: z.string() })
export type MailAddress = z.infer<typeof addressSchema>

const flagsSchema = z.object({
  seen: z.boolean(),
  flagged: z.boolean(),
  answered: z.boolean(),
  draft: z.boolean(),
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
})
export type MailFolder = z.infer<typeof mailFolderSchema>

const folderListSchema = z.object({ account_id: z.string(), folders: z.array(mailFolderSchema) })

const messageItemSchema = z.object({
  ref: z.string(),
  account_id: z.string(),
  folder: z.string(),
  date: z.string().nullable(),
  from: addressSchema,
  subject: z.string(),
  flags: flagsSchema,
  has_attachments: z.boolean(),
  preview: z.string(),
  size: z.number(),
})
export type MessageItem = z.infer<typeof messageItemSchema>

const failureSchema = z.object({ account_id: z.string(), folder: z.string().nullable(), code: z.string() })
export type SourceFailure = z.infer<typeof failureSchema>

const messagePageSchema = z.object({
  messages: z.array(messageItemSchema),
  next_cursor: z.string().nullable(),
  failures: z.array(failureSchema),
})
export type MessagePage = z.infer<typeof messagePageSchema>

const partSchema = z.object({ part: z.string(), name: z.string(), type: z.string(), size: z.number() })
export type MessagePart = z.infer<typeof partSchema>

const messageViewSchema = z.object({
  ref: z.string(),
  account_id: z.string(),
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
  has_remote_content: z.boolean(),
  attachments: z.array(partSchema),
  calendar: partSchema.nullable(),
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
export const SEARCH_FLAG_FIELDS = ['unread', 'flagged', 'has_attachment'] as const

const base = '/api/v1/mail'
const accountPath = (id: string): string => `${base}/accounts/${encodeURIComponent(id)}`
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
export async function listMailFolders(accountId: string, signal?: AbortSignal): Promise<MailFolder[]> {
  const list = await apiGetJson(`${accountPath(accountId)}/folders?all=true`, {
    init: { signal },
    schema: folderListSchema,
  })
  return list.folders
}

const folderCreatedSchema = z.object({ account_id: z.string(), path: z.string() })

const folderRenamedSchema = z.object({
  account_id: z.string(),
  /** The old path. */
  folder: z.string(),
  path: z.string(),
  /** The rule sets and filters pointed at the new path. */
  rule_sets: z.array(z.number()),
  filters: z.array(z.number()),
  /** `false`: the folder was renamed but its rule sets still name the old path. */
  rule_sets_updated: z.boolean(),
})
export type FolderRenamed = z.infer<typeof folderRenamedSchema>

const folderDeletedSchema = z.object({ account_id: z.string(), folder: z.string(), messages: z.number() })

/** Creates `name` under `parent` (`null`: the top level); the new folder's path. */
export async function createFolder(accountId: string, parent: string | null, name: string): Promise<string> {
  const created = await apiPostJson(`${accountPath(accountId)}/folders`, { parent, name }, { schema: folderCreatedSchema })
  return created.path
}

/** Renames `folder` to `name` under `parent`, its subfolders and mail with it, and its rule sets. */
export function renameFolder(accountId: string, folder: string, parent: string | null, name: string): Promise<FolderRenamed> {
  return apiPostJson(`${accountPath(accountId)}/folders/rename`, { folder, parent, name }, { schema: folderRenamedSchema })
}

/**
 * Deletes `folder` and its mail for good. `messages` is the count the user confirmed: a different
 * count now is refused as `folder_changed`, carrying it.
 */
export async function deleteFolder(accountId: string, folder: string, messages: number): Promise<void> {
  await apiPostJson(`${accountPath(accountId)}/folders/delete`, { folder, messages }, { schema: folderDeletedSchema })
}

export function listMessages(
  accountId: string,
  options: { folder: string; cursor?: string | null; limit?: number },
  signal?: AbortSignal,
): Promise<MessagePage> {
  const path = withQuery(`${accountPath(accountId)}/messages`, {
    folder: options.folder,
    limit: options.limit,
    cursor: options.cursor ?? undefined,
  })
  return apiGetJson(path, { init: { signal }, schema: messagePageSchema })
}

export function searchMessages(
  query: SearchQuery,
  options: { cursor?: string | null; limit?: number } = {},
  signal?: AbortSignal,
): Promise<MessagePage> {
  // A body, not a query string: search terms stay out of access logs.
  const body = { ...query, limit: options.limit, cursor: options.cursor ?? undefined }
  return apiPostJson(`${base}/search`, body, { init: { signal }, schema: messagePageSchema })
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

/** The standalone, sanitised HTML document of a message, for a sandboxed iframe's `src`. */
export function messageHtmlUrl(ref: string, remote: boolean): string {
  return `${getApiBaseUrl()}${messagePath(ref)}/html${remote ? '?remote=1' : ''}`
}

/**
 * Asks for the HTML document before a frame shows it: a refusal then throws, as any API call's,
 * for the client to word, instead of showing up inside the frame as the API's JSON.
 */
export async function checkMessageHtml(ref: string, remote: boolean, signal?: AbortSignal): Promise<void> {
  const response = await fetch(messageHtmlUrl(ref, remote), { credentials: 'include', signal })
  if (response.ok) {
    await response.body?.cancel()
    return
  }
  if (response.status === 401) {
    reportSessionEnded()
  }
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    // Not the API's JSON (a proxy's page): the status alone words it.
  }
  throw new ApiRequestError(response.status, body, `HTTP ${String(response.status)}`)
}

/** A download of one attachment (or the calendar part). */
export function messagePartUrl(ref: string, part: string): string {
  return `${getApiBaseUrl()}${messagePath(ref)}/parts/${encodeURIComponent(part)}`
}

const organizeGroupSchema = z.object({
  account: z.string(),
  folder: z.string(),
  ok: z.boolean(),
  count: z.number(),
  new_refs: z.array(z.string()).nullable(),
  junk: z.enum(['spam', 'ham']).nullable(),
  flags: z.array(z.object({ ref: z.string(), seen: z.boolean().nullable(), flagged: z.boolean().nullable() })),
  error: z.object({ code: z.string(), message: z.string() }).nullable(),
})
/** One account and folder of a batch: groups succeed or fail on their own. */
export type OrganizeGroup = z.infer<typeof organizeGroupSchema>

const organizeAnswerSchema = z.object({ results: z.array(organizeGroupSchema) })

/** The flags a batch sets; at least one. */
export interface FlagChange {
  seen?: boolean
  flagged?: boolean
}

async function organize(route: string, body: object, signal?: AbortSignal): Promise<OrganizeGroup[]> {
  const answer = await apiPostJson(`${base}/messages/${route}`, body, { init: { signal }, schema: organizeAnswerSchema })
  return answer.results
}

/**
 * Up to 50 refs, from any folders; a group's failure is in its result, and only a batch where no
 * group succeeded throws (the first failure's refusal).
 */
export function moveMessages(refs: string[], folder: string, signal?: AbortSignal): Promise<OrganizeGroup[]> {
  return organize('move', { refs, folder }, signal)
}

export function archiveMessages(refs: string[], signal?: AbortSignal): Promise<OrganizeGroup[]> {
  return organize('archive', { refs }, signal)
}

/** Moves them to Trash, never deleting them. */
export function trashMessages(refs: string[], signal?: AbortSignal): Promise<OrganizeGroup[]> {
  return organize('trash', { refs }, signal)
}

export function flagMessages(refs: string[], change: FlagChange, signal?: AbortSignal): Promise<OrganizeGroup[]> {
  return organize('flag', { refs, ...change }, signal)
}

const keepPartSchema = partSchema.extend({ ref: z.string() })
/** A part of another message (or of the draft itself) a draft keeps. */
export type KeptPart = z.infer<typeof keepPartSchema>

const composeFieldsSchema = z.object({
  account: z.string(),
  from: z.string().nullable(),
  to: z.array(addressSchema),
  cc: z.array(addressSchema),
  bcc: z.array(addressSchema),
  subject: z.string(),
  text: z.string(),
  answers: z.string().nullable(),
})

const prefillSchema = composeFieldsSchema.extend({ attachments: z.array(keepPartSchema) })
export type Prefill = z.infer<typeof prefillSchema>
export type PrefillMode = 'reply' | 'reply_all' | 'forward'

export const SEND_STATES = ['sending', 'sent', 'failed', 'unknown'] as const

const draftSendSchema = z.object({ send_id: z.string(), state: z.enum(SEND_STATES), code: z.string().nullable() })
export type DraftSend = z.infer<typeof draftSendSchema>

const draftSchema = composeFieldsSchema.extend({
  ref: z.string(),
  compose_id: z.string().nullable(),
  lossy: z.boolean(),
  parts: z.array(partSchema),
  send: draftSendSchema.nullable(),
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
  replaced: z.boolean(),
  autosave: z.boolean(),
})
export type SavedDraft = z.infer<typeof savedDraftSchema>

const foundDraftSchema = z.object({ ref: z.string(), parts: z.array(partSchema) })
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

/** The `draft` part of a save. */
export interface DraftFields {
  account: string
  compose_id: string
  ref: string | null
  answers: string | null
  from: string | null
  to: MailAddress[]
  cc: MailAddress[]
  bcc: MailAddress[]
  subject: string
  text: string
  keep: { ref: string; part: string }[]
}

const draftPath = (ref: string): string => `${base}/drafts/${encodeURIComponent(ref)}`

/** The fields a reply or forward of `ref` starts from; writes nothing. */
export function prefillDraft(ref: string, mode: PrefillMode, signal?: AbortSignal): Promise<Prefill> {
  return apiPostJson(`${base}/drafts/prefill`, { ref, mode }, { init: { signal }, schema: prefillSchema })
}

/**
 * Replaces the draft `fields.ref` (or writes the first version). `keepalive` outlives the page, so
 * it must fit the browser's small keepalive quota: text only, no `files`.
 */
export function saveDraft(fields: DraftFields, files: File[], options: { keepalive?: boolean } = {}): Promise<SavedDraft> {
  const form = new FormData()
  form.set('draft', JSON.stringify(fields))
  for (const file of files) {
    form.append('files[]', file)
  }
  return apiPostForm(`${base}/drafts`, form, { init: { keepalive: options.keepalive === true }, schema: savedDraftSchema })
}

export function readDraft(ref: string, signal?: AbortSignal): Promise<Draft> {
  return apiGetJson(draftPath(ref), { init: { signal }, schema: draftSchema })
}

/** The newest version saved under `composeId`, or `null` when there is none. */
export async function findDraft(account: string, composeId: string, signal?: AbortSignal): Promise<FoundDraft | null> {
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
export function sendDraft(ref: string, options: { confirmResend?: boolean } = {}): Promise<SendResult | { send_id: string }> {
  const body = options.confirmResend === true ? { confirm_resend: true } : {}
  return apiPostJson(`${draftPath(ref)}/send`, body, {
    schema: z.union([sendResultSchema, z.object({ send_id: z.string() })]),
  })
}

export function readSend(sendId: string, signal?: AbortSignal): Promise<SendResult> {
  return apiGetJson(`${base}/sends/${encodeURIComponent(sendId)}`, { init: { signal }, schema: sendResultSchema })
}

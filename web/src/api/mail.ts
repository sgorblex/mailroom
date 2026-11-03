import { z } from 'zod'

import { getApiBaseUrl } from './api-base-url.ts'
import { ApiRequestError, apiGetJson, apiPostJson, reportSessionEnded } from './http.ts'

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

export async function listMailFolders(accountId: string, signal?: AbortSignal): Promise<MailFolder[]> {
  const list = await apiGetJson(`${accountPath(accountId)}/folders`, {
    init: { signal },
    schema: folderListSchema,
  })
  return list.folders
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

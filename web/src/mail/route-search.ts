import type { AccountId } from '@/api/accounts.ts'
import { ALL_INBOXES, SEARCH_FLAG_FIELDS, SEARCH_TEXT_FIELDS, type SearchQuery, type ViewKey } from '@/api/mail.ts'

/** The quick filters above the list, each a search criterion set to true. */
export const QUICK_FILTERS = ['unread', 'flagged', 'has_attachment'] as const
export type QuickFilter = (typeof QUICK_FILTERS)[number]

/**
 * Where the mail screen is, kept in the URL so a reload or a shared link lands in the same place:
 * the account and folder shown, whether with its subfolders (`sub`; absent, as this browser last
 * listed it), the open message (its ref) and, when it was opened as a conversation's row, that
 * row's ref (`thread`), a search or a view (`view`) in place of the folder, the
 * quick filters narrowing a folder or a search (`only`), and the composer: `compose-session.ts` says what `compose` holds.
 * Kept out of the screen's own chunk: the router validates it before the screen loads.
 */
export interface MailSearch {
  account?: AccountId
  folder?: string
  sub?: boolean
  message?: string
  thread?: string
  q?: SearchQuery
  view?: ViewKey
  only?: QuickFilter[]
  compose?: string
}

export function text(value: unknown): string | undefined {
  // The router parses `?folder=2026` as a number: a folder name it is all the same.
  if (typeof value === 'number') {
    return String(value)
  }
  return typeof value === 'string' && value !== '' ? value : undefined
}

/** A positive safe integer, given as one or as its digits: an account's or a saved view's id. */
function positiveId(value: unknown): number | undefined {
  const number = typeof value === 'string' && /^[1-9][0-9]*$/.test(value) ? Number(value) : value
  return typeof number === 'number' && Number.isSafeInteger(number) && number > 0 ? number : undefined
}

export function accountId(value: unknown): AccountId | undefined {
  return positiveId(value)
}

export function searchQuery(value: unknown): SearchQuery | undefined {
  if (typeof value !== 'object' || value === null) {
    return undefined
  }
  const fields = value as Record<string, unknown>
  const query: SearchQuery = {}
  for (const key of SEARCH_TEXT_FIELDS) {
    const field = text(fields[key])
    if (field !== undefined) {
      query[key] = field
    }
  }
  // A search saved before ids were numbers names its account by a slug: searched by default instead.
  if (query.account !== undefined && query.account !== '*' && !/^[1-9][0-9]*$/.test(query.account)) {
    delete query.account
  }
  for (const key of SEARCH_FLAG_FIELDS) {
    // `false` only where the syntax can write it: `is:read`.
    const flag = fields[key]
    if (flag === true || (key === 'unread' && flag === false)) {
      query[key] = flag
    }
  }
  return query
}

/** A saved view's id, or `all_inboxes`. */
export function viewKey(value: unknown): ViewKey | undefined {
  return value === ALL_INBOXES ? ALL_INBOXES : positiveId(value)
}

/** The quick filters named, once each and in their own order; `undefined` for none. */
export function quickFilters(value: unknown): QuickFilter[] | undefined {
  const named = Array.isArray(value) ? value : [value]
  const only = QUICK_FILTERS.filter((filter) => named.includes(filter))
  return only.length > 0 ? only : undefined
}

/**
 * The search the list runs: the URL's, or the folder shown as one, narrowed by the quick filters;
 * `undefined` for the folder itself.
 */
export function listQuery(
  account: AccountId,
  folder: string,
  q: SearchQuery | undefined,
  only: readonly QuickFilter[] | undefined,
): SearchQuery | undefined {
  if (only === undefined || only.length === 0) {
    return q
  }
  const query: SearchQuery = q === undefined ? { account: String(account), folder } : { ...q }
  for (const filter of only) {
    query[filter] = true
  }
  return query
}

export function validateMailSearch(raw: Record<string, unknown>): MailSearch {
  const search: MailSearch = {}
  const account = accountId(raw['account'])
  const folder = text(raw['folder'])
  const message = text(raw['message'])
  const thread = text(raw['thread'])
  const q = searchQuery(raw['q'])
  const view = viewKey(raw['view'])
  const only = quickFilters(raw['only'])
  const compose = text(raw['compose'])
  if (account !== undefined) {
    search.account = account
  }
  if (folder !== undefined) {
    search.folder = folder
  }
  if (typeof raw['sub'] === 'boolean') {
    search.sub = raw['sub']
  }
  if (message !== undefined) {
    search.message = message
  }
  if (thread !== undefined && message !== undefined) {
    search.thread = thread
  }
  if (q !== undefined) {
    search.q = q
  }
  if (view !== undefined) {
    search.view = view
  }
  if (only !== undefined) {
    search.only = only
  }
  if (compose !== undefined) {
    search.compose = compose
  }
  return search
}

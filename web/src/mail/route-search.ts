import type { AccountId } from '@/api/accounts.ts'
import { SEARCH_FLAG_FIELDS, SEARCH_TEXT_FIELDS, type SearchQuery } from '@/api/mail.ts'

/**
 * Where the mail screen is, kept in the URL so a reload or a shared link lands in the same place:
 * the account and folder shown, whether with its subfolders (`sub`; absent, as this browser last
 * listed it), the open message (its ref), a search in place of the folder, and the composer:
 * `compose-session.ts` says what `compose` holds.
 * Kept out of the screen's own chunk: the router validates it before the screen loads.
 */
export interface MailSearch {
  account?: AccountId
  folder?: string
  sub?: boolean
  message?: string
  q?: SearchQuery
  compose?: string
}

export function text(value: unknown): string | undefined {
  // The router parses `?folder=2026` as a number: a folder name it is all the same.
  if (typeof value === 'number') {
    return String(value)
  }
  return typeof value === 'string' && value !== '' ? value : undefined
}

export function accountId(value: unknown): AccountId | undefined {
  const number = typeof value === 'string' && /^[1-9][0-9]*$/.test(value) ? Number(value) : value
  return typeof number === 'number' && Number.isSafeInteger(number) && number > 0 ? number : undefined
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

export function validateMailSearch(raw: Record<string, unknown>): MailSearch {
  const search: MailSearch = {}
  const account = accountId(raw['account'])
  const folder = text(raw['folder'])
  const message = text(raw['message'])
  const q = searchQuery(raw['q'])
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
  if (q !== undefined) {
    search.q = q
  }
  if (compose !== undefined) {
    search.compose = compose
  }
  return search
}

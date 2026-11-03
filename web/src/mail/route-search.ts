import { SEARCH_FLAG_FIELDS, SEARCH_TEXT_FIELDS, type SearchQuery } from '@/api/mail.ts'

/**
 * Where the mail screen is, kept in the URL so a reload or a shared link lands in the same place:
 * the account and folder shown, the open message (its ref), and a search in place of the folder.
 * Kept out of the screen's own chunk: the router validates it before the screen loads.
 */
export interface MailSearch {
  account?: string
  folder?: string
  message?: string
  q?: SearchQuery
}

function text(value: unknown): string | undefined {
  // The router parses `?folder=2026` as a number: a folder name it is all the same.
  if (typeof value === 'number') {
    return String(value)
  }
  return typeof value === 'string' && value !== '' ? value : undefined
}

function searchQuery(value: unknown): SearchQuery | undefined {
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
  for (const key of SEARCH_FLAG_FIELDS) {
    if (fields[key] === true) {
      query[key] = true
    }
  }
  return query
}

export function validateMailSearch(raw: Record<string, unknown>): MailSearch {
  const search: MailSearch = {}
  const account = text(raw['account'])
  const folder = text(raw['folder'])
  const message = text(raw['message'])
  const q = searchQuery(raw['q'])
  if (account !== undefined) {
    search.account = account
  }
  if (folder !== undefined) {
    search.folder = folder
  }
  if (message !== undefined) {
    search.message = message
  }
  if (q !== undefined) {
    search.q = q
  }
  return search
}

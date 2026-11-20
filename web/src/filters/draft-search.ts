import type { AccountId } from '@/api/accounts.ts'
import type { SearchQuery } from '@/api/mail.ts'
import { accountId, searchQuery, text } from '@/mail/route-search.ts'

/**
 * What `/filters/new` drafts a filter from, kept in the URL as the mail screen keeps its own: a
 * message (its ref and account) or a search. Kept out of the screen's chunk: the router validates
 * it before the screen loads.
 */
export interface FilterDraftSearch {
  account?: AccountId
  message?: string
  q?: SearchQuery
}

export function validateFilterDraftSearch(raw: Record<string, unknown>): FilterDraftSearch {
  const search: FilterDraftSearch = {}
  const account = accountId(raw['account'])
  const message = text(raw['message'])
  const q = searchQuery(raw['q'])
  if (account !== undefined) {
    search.account = account
  }
  if (message !== undefined) {
    search.message = message
  }
  if (q !== undefined) {
    search.q = q
  }
  return search
}

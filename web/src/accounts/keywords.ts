import type { UseQueryOptions } from '@tanstack/react-query'

import { listAccountKeywords, type AccountId, type AccountKeywords } from '@/api/accounts.ts'

/** Whether each account's server keeps keywords (`GET /accounts/keywords`). */
export const KEYWORDS_KEY = ['account-keywords'] as const

/**
 * How long an answer is taken as it stands: as long as the server leaves alone an account that
 * failed, and long enough that every row of a list asking it reads no more than once.
 */
const KEYWORDS_STALE_MS = 5 * 60_000

export function keywordsQuery(): UseQueryOptions<AccountKeywords[]> {
  return { queryKey: KEYWORDS_KEY, queryFn: ({ signal }) => listAccountKeywords(signal), staleTime: KEYWORDS_STALE_MS }
}

/** Whether the account's server keeps keywords, from `known`; `null` when not known (yet). */
export function accountKeywords(known: readonly AccountKeywords[] | undefined, id: AccountId): boolean | null {
  return known?.find((each) => each.account_id === id)?.keywords ?? null
}

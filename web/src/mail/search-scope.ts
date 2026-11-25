import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'

import type { Account, AccountId } from '@/api/accounts.ts'
import { ALL_INBOXES, type SavedView, type SearchQuery, type ViewKey } from '@/api/mail.ts'

import { viewsQuery } from './queries.ts'
import { lineOf, namedAccount, parseSearch } from './search-syntax.ts'

/** Where a line that names no account and no folder searches: the folder or the view open. */
export interface SearchScope {
  /** An account's id, or `*`. */
  account: string
  folder: string
  /** The folder is listed, so searched, with its subfolders. */
  subfolders: boolean
  /** A view of several folders, which are then searched in place of `account` and `folder`. */
  view: Pick<SavedView, 'id' | 'name'> | null
  /** The terms the box starts with: the criteria of a view that is a search. */
  line: string
}

const EVERY_INBOX: SearchScope = { account: '*', folder: 'INBOX', subfolders: false, view: null, line: '' }
export const ALL_MAIL: SearchScope = { ...EVERY_INBOX, folder: '*' }

/** The scope of `view`, opened over the accounts; all mail for one that is gone or no longer reads. */
export function viewScope(view: typeof ALL_INBOXES | SavedView | undefined, accounts: Account[]): SearchScope {
  if (view === ALL_INBOXES) {
    return EVERY_INBOX
  }
  if (view === undefined) {
    return ALL_MAIL
  }
  if (view.sources !== null) {
    return { ...EVERY_INBOX, view: { id: view.id, name: view.name } }
  }
  const parsed = parseSearch(view.query ?? '')
  if (!parsed.ok) {
    return ALL_MAIL
  }
  const { account, folder, ...criteria } = parsed.parsed
  const named = namedAccount(accounts, account ?? '*')
  return named.ok ? { ...EVERY_INBOX, account: named.account, folder: folder ?? 'INBOX', line: lineOf(criteria) } : ALL_MAIL
}

/** The scope of the folder or the view open; `null` while the saved views load. */
export function useSearchScope(
  accounts: Account[],
  accountId: AccountId,
  folder: string,
  subfolders: boolean,
  view: ViewKey | undefined,
): SearchScope | null {
  const { data, isPending } = useQuery(viewsQuery())
  return useMemo(() => {
    if (view === undefined) {
      return { account: String(accountId), folder, subfolders, view: null, line: '' }
    }
    if (view !== ALL_INBOXES && isPending) {
      return null
    }
    return viewScope(view === ALL_INBOXES ? view : data?.find((each) => each.id === view), accounts)
  }, [accounts, accountId, folder, subfolders, view, data, isPending])
}

/** What `query` searches as the line's `account:` and `in:`, the default account and folder included; none for a view's folders. */
export function searchedIn(query: SearchQuery): { account: string; folder: string } | null {
  return query.view === undefined ? { account: query.account ?? '*', folder: query.folder ?? 'INBOX' } : null
}

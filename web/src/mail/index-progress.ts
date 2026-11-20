import type { AccountId } from '@/api/accounts.ts'
import type { IndexFolder, IndexStatus, SearchQuery } from '@/api/mail.ts'

import { isSameFolder } from './triage-context.ts'

/** What a list or a search reads: one folder (with its subfolders), or every folder (`*`). */
export interface IndexScope {
  accounts: readonly AccountId[]
  folder: string
  /** The folder's subfolders are read too, split on `delimiter`. */
  subfolders: boolean
  delimiter: string | null
}

/** How far the first sync of the folders still being indexed has come. */
export interface IndexProgress {
  indexed: number
  /** The server's count; `null` unless every waiting folder's is known. */
  total: number | null
}

/** The scope of the list shown, or of `query`; `null` when the index never answers it (a body's text, Gmail's syntax). */
export function indexScope(
  accounts: readonly AccountId[],
  accountId: AccountId,
  folder: string,
  subfolders: boolean,
  delimiter: string | null,
  query: SearchQuery | null,
): IndexScope | null {
  if (query === null) {
    return { accounts: [accountId], folder, subfolders, delimiter }
  }
  if (query.text !== undefined || query.gmail_query !== undefined) {
    return null
  }
  const account = query.account ?? '*'
  return {
    accounts: account === '*' ? accounts : accounts.filter((each) => String(each) === account),
    folder: query.folder ?? 'INBOX',
    subfolders,
    delimiter,
  }
}

/**
 * The indexed folders `status` reads for `scope`, by path: a Gmail label is All Mail's; every
 * folder (`*`) is the `\All` folder where there is one, else all but Junk and Trash, as a search.
 */
function sources(status: IndexStatus, scope: IndexScope): IndexFolder[] {
  const allMail = status.folders.find((each) => each.role === 'all' && each.indexed_as === 'folder')
  const read =
    scope.folder === '*'
      ? allMail !== undefined
        ? [allMail]
        : status.folders.filter((each) => each.role !== 'junk' && each.role !== 'trash')
      : status.folders.filter(
          (each) =>
            isSameFolder(each.path, scope.folder) ||
            (scope.subfolders && scope.delimiter !== null && each.path.startsWith(scope.folder + scope.delimiter)),
        )
  const found = new Map<string, IndexFolder>()
  for (const each of read) {
    const source = each.indexed_as === 'label' ? allMail : each.indexed_as === 'folder' ? each : undefined
    if (source !== undefined) {
      found.set(source.path, source)
    }
  }
  return [...found.values()]
}

/** The progress of the folders of `scope` waiting for or in their first sync; `null` when none is. */
export function indexProgress(statuses: readonly IndexStatus[], scope: IndexScope): IndexProgress | null {
  const waiting = statuses
    .flatMap((status) => sources(status, scope))
    .filter((each) => each.enabled && (each.state === 'pending' || each.state === 'syncing'))
  if (waiting.length === 0) {
    return null
  }
  return {
    indexed: waiting.reduce((sum, each) => sum + (each.indexed ?? 0), 0),
    // Summed only when every one is known: a partial sum could read "1,500 of 1,000".
    total: waiting.every((each) => each.total !== null) ? waiting.reduce((sum, each) => sum + (each.total ?? 0), 0) : null,
  }
}

import type { Account, AccountId } from '@/api/accounts.ts'
import { ALL_INBOXES, type MailFolder, type SavedView, type SearchQuery, type ViewKey } from '@/api/mail.ts'

import { findNode, folderTree, listedSubfolders } from './folder-tree.ts'
import type { IndexScope } from './index-progress.ts'
import { lineOf, namedAccount, parseSearch } from './search-syntax.ts'
import { isSameFolder } from './triage-context.ts'

/** A view as the screen reads it: *All inboxes*, or one of the user's saved views. */
export type ShownView = typeof ALL_INBOXES | SavedView

/** Names a view's list (`inboxSubfolders`: *All inboxes* with INBOX's subfolders), for its pages' query keys and the triage scope. */
export function viewSource(view: ViewKey, inboxSubfolders: boolean): string {
  return `view:${String(view)}${view === ALL_INBOXES && inboxSubfolders ? ':subfolders' : ''}`
}

/** The account's folders as already listed, if they are. */
type Listed = (account: AccountId) => readonly MailFolder[] | undefined

/** INBOX's subfolders among an account's `folders`, as the server picks them for *All inboxes*: at any depth, none special. */
function inboxSubfolders(folders: readonly MailFolder[] | undefined): string[] {
  const inbox = findNode(folderTree(folders ?? []), 'INBOX')
  return inbox === undefined ? [] : listedSubfolders(inbox).flatMap(({ folder }) => (folder === null || folder.role === 'flagged' ? [] : [folder.path]))
}

/**
 * `query` as a line a view can keep; `null` when it cannot be one: words to search or Gmail's own
 * syntax (the index searches neither), a view it is searched in, or a double quote the syntax cannot write.
 */
export function savableLine(query: SearchQuery): string | null {
  const { gmail_query: gmailQuery, view, ...parsed } = query
  if (view !== undefined || parsed.text !== undefined || gmailQuery !== undefined) {
    return null
  }
  if (Object.values(parsed).some((value) => typeof value === 'string' && value.includes('"'))) {
    return null
  }
  const line = lineOf(parsed)
  return line === '' ? null : line
}

/** The folders a view of folders, or *All inboxes* (with INBOX's subfolders among those `listed`), reads; `null` for a search. */
function viewFolders(view: ShownView, accounts: readonly AccountId[], listed: Listed | null): { account: AccountId; folder: string }[] | null {
  if (view === ALL_INBOXES) {
    return accounts.flatMap((account) =>
      ['INBOX', ...(listed === null ? [] : inboxSubfolders(listed(account)))].map((folder) => ({ account, folder })),
    )
  }
  return view.sources?.map((source) => ({ account: source.account_id, folder: source.folder })) ?? null
}

/**
 * The unread count of a view's folders, from the folder lists already read (`folders`); `null`
 * for a search, or while one of its accounts' folders is not listed yet. A folder no longer there
 * counts nothing. `withSubfolders`: *All inboxes* takes in INBOX's subfolders.
 */
export function viewUnread(
  view: ShownView,
  accounts: readonly AccountId[],
  folders: Listed,
  withSubfolders = false,
): number | null {
  const read = viewFolders(view, accounts, withSubfolders ? folders : null)
  if (read === null) {
    return null
  }
  let unread = 0
  for (const { account, folder } of read) {
    const listed = folders(account)
    if (listed === undefined) {
      return null
    }
    unread += listed.find((each) => isSameFolder(each.path, folder))?.unread ?? 0
  }
  return unread
}

/**
 * The folder a view's actions are taken from, which a Gmail row (listed from All Mail's) is acted
 * on in: INBOX for *All inboxes*, none when it takes in subfolders; a search's `in:` (INBOX by default, none over every folder); its
 * folders' path when they all share it; else none. A row naming its own (`listed_folder`) is
 * acted on there whatever this says.
 */
export function viewFolder(view: ShownView | undefined, withSubfolders = false): string | undefined {
  if (view === undefined) {
    return undefined
  }
  if (view === ALL_INBOXES) {
    return withSubfolders ? undefined : 'INBOX'
  }
  if (view.sources !== null) {
    const [first, ...rest] = view.sources
    return first !== undefined && rest.every((source) => isSameFolder(source.folder, first.folder)) ? first.folder : undefined
  }
  const parsed = view.query === null ? null : parseSearch(view.query)
  if (!parsed?.ok) {
    return undefined
  }
  const folder = parsed.parsed.folder ?? 'INBOX'
  return folder === '*' ? undefined : folder
}

/**
 * What a view reads, as scopes live events are matched against: each folder, or the search's
 * folder (INBOX by default) of its account (every account by default); none for a search that no
 * longer reads. `listed`, for *All inboxes* with INBOX's subfolders: those of the folders listed.
 */
export function viewScopes(view: ShownView, accounts: readonly Account[], listed: Listed | null = null): IndexScope[] {
  const ids = accounts.map((account) => account.id)
  const scope = (scoped: readonly AccountId[], folder: string): IndexScope => ({
    accounts: scoped,
    folder,
    subfolders: false,
    delimiter: null,
  })
  const read = viewFolders(view, ids, listed)
  if (read !== null) {
    return read.map(({ account, folder }) => scope([account], folder))
  }
  if (view === ALL_INBOXES || view.query === null) {
    return []
  }
  const parsed = parseSearch(view.query)
  if (!parsed.ok) {
    return []
  }
  const { account, folder } = parsed.parsed
  const named = account === undefined ? { ok: true as const, account: '*' } : namedAccount([...accounts], account)
  if (!named.ok) {
    return []
  }
  return [scope(named.account === '*' ? ids : ids.filter((id) => String(id) === named.account), folder ?? 'INBOX')]
}

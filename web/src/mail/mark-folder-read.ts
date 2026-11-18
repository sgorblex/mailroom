import type { TFunction } from 'i18next'

import type { AccountId } from '@/api/accounts.ts'
import { ApiRequestError } from '@/api/http.ts'
import { markFolderRead, type MailFolder } from '@/api/mail.ts'

import { type FolderNode, listedSubfolders } from './folder-tree.ts'
import { mailErrorMessage } from './messages.ts'

/** Above this many unread, marking a folder read asks first. */
export const MARK_READ_CONFIRM_ABOVE = 50

/**
 * A folder to mark read, from its menu: `folders` are those of it the listing counted unread
 * mail in, the folder alone or, with `subfolders`, also the subfolders a list of it reads
 * (`listedSubfolders`: none with a role kept apart). `unread` is theirs summed.
 */
export interface MarkReadScope {
  folder: MailFolder
  subfolders: boolean
  folders: MailFolder[]
  unread: number
}

/** What a mark-read of a scope's folders did: the count marked, the folders done, and those refused. */
export interface MarkReadOutcome {
  marked: number
  done: string[]
  failed: { folder: MailFolder; error: unknown }[]
}

/** The scope of marking `node` read, with its listed subfolders or alone. */
export function markReadScope(node: FolderNode & { folder: MailFolder }, subfolders: boolean): MarkReadScope {
  const all = subfolders
    ? [...(node.folder.selectable ? [node.folder] : []), ...listedSubfolders(node).flatMap((child) => (child.folder === null ? [] : [child.folder]))]
    : [node.folder]
  const folders = all.filter((folder) => folder.selectable && folder.unread > 0)
  return { folder: node.folder, subfolders, folders, unread: folders.reduce((sum, folder) => sum + folder.unread, 0) }
}

/** Whether every folder of `scope` came with its UIDs, which a mark-read sends back. */
export function hasUids(scope: MarkReadScope): boolean {
  return scope.folders.every((folder) => folder.uid_validity !== null && folder.uid_next !== null)
}

/**
 * Marks `folder` read as the listing the user acted on had it: mail that arrived since stays
 * unread. Only for a folder whose listing gave its UIDs. The count marked.
 */
export function markRead(accountId: AccountId, folder: MailFolder): Promise<number> {
  if (folder.uid_validity === null || folder.uid_next === null) {
    return Promise.reject(new Error('markRead on a folder without its UIDs'))
  }
  return markFolderRead(accountId, folder.path, folder.uid_validity, folder.uid_next)
}

/**
 * Marks each of `scope`'s folders read, one call per folder, each bounded by its own listing's
 * UIDNEXT; one refused leaves the others to go on.
 */
export async function markScopeRead(accountId: AccountId, scope: MarkReadScope): Promise<MarkReadOutcome> {
  const outcome: MarkReadOutcome = { marked: 0, done: [], failed: [] }
  for (const folder of scope.folders) {
    try {
      outcome.marked += await markRead(accountId, folder)
      outcome.done.push(folder.path)
    } catch (error) {
      outcome.failed.push({ folder, error })
    }
  }
  return outcome
}

/** Whether a mark-read was refused because the folder changed since its listing: it must be listed again. */
export function isStale(error: unknown): boolean {
  return error instanceof ApiRequestError && error.code === 'stale_reference'
}

/** A refused mark-read in the reader's language. */
export function markReadError(t: TFunction, error: unknown): string {
  return isStale(error) ? t('folderActions.markReadStale') : mailErrorMessage(t, error, t('folderActions.failed'))
}

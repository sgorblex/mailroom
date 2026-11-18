import { useEffect } from 'react'

import { useAccountKeywords } from '@/accounts/accounts-context.ts'
import type { AccountId } from '@/api/accounts.ts'
import type { MailFolder, MoveRemoval } from '@/api/mail.ts'

import { isSameFolder, loadedFolders, roleFolder, useTriage, type Target } from './triage-context.ts'

/** The actions the toolbar and the shortcuts offer for the current targets; `null` is not offered. */
export interface TriageActions {
  targets: Target[]
  archive: (() => void) | null
  trash: (() => void) | null
  /** Star, or unstar when every target is starred: `flagged` is what it sets. */
  star: { flagged: boolean; run: () => void } | null
  /**
   * Mark To sort, or sorted when every target is marked: `toSort` is what it sets. `unsupported`
   * when a target's account keeps no keywords: offered greyed out, and not to be run.
   */
  toSort: { toSort: boolean; unsupported: boolean; run: () => void } | null
  /** Mark read when a target is unread, else mark unread: `seen` is what it sets. */
  read: { seen: boolean; run: () => void } | null
  markUnread: (() => void) | null
  /** The folders to move to: the account's selectable ones, without Drafts, Sent and the folder they are in. */
  moveChoices: MailFolder[] | null
  move: (folder: MailFolder) => void
  /** The user's other accounts, none of which holds a target, to move to; `null` when there is none. */
  otherAccounts: AccountId[] | null
  moveToAccount: (account: AccountId, folder: MailFolder, removal: MoveRemoval) => void
}

/** Nothing to act on. */
export const NO_TRIAGE_ACTIONS: TriageActions = {
  targets: [],
  archive: null,
  trash: null,
  star: null,
  toSort: null,
  read: null,
  markUnread: null,
  moveChoices: null,
  move: () => undefined,
  otherAccounts: null,
  moveToAccount: () => undefined,
}

/** Where a message may be moved to in an account: a selectable folder other than Drafts and Sent. */
export function movableInto(folder: MailFolder): boolean {
  return folder.selectable && folder.role !== 'drafts' && folder.role !== 'sent'
}

/** The actions on `of`, by default what the toolbar acts on (the selection, else the open message). */
export function useTriageActions(of?: Target[]): TriageActions {
  const triage = useTriage()
  const { run, flagsOf, loadFolders } = triage
  const targets = of ?? triage.targets
  const keywordsOf = useAccountKeywords()
  const accounts = [...new Set(targets.map((target) => target.account_id))]
  const accountsKey = JSON.stringify(accounts)

  useEffect(() => {
    for (const account of JSON.parse(accountsKey) as AccountId[]) {
      loadFolders(account, false)
    }
  }, [accountsKey, loadFolders])

  const foldersOf = (account: AccountId): MailFolder[] | undefined => loadedFolders(triage.folders[account])
  const outside = (role: 'archive' | 'trash'): Target[] =>
    targets.filter((target) => {
      const where = roleFolder(foldersOf(target.account_id), role)?.path
      return where === undefined || !isSameFolder(target.folder, where)
    })

  if (targets.length === 0) {
    return NO_TRIAGE_ACTIONS
  }

  const canArchive =
    accounts.every((account) => roleFolder(foldersOf(account), 'archive') !== undefined) && outside('archive').length > 0
  const flagged = !targets.every((target) => flagsOf(target).flagged)
  const toSort = !targets.every((target) => flagsOf(target).to_sort)
  const toSortUnsupported = accounts.some((each) => keywordsOf(each) === false)
  const seen = targets.some((target) => !flagsOf(target).seen)
  const [account] = accounts
  const folders = accounts.length === 1 && account !== undefined ? foldersOf(account) : undefined
  const sources = [...new Set(targets.map((target) => target.folder))]
  const others = triage.accounts.filter((each) => !accounts.includes(each))
  const [source] = sources
  return {
    targets,
    archive: canArchive ? () => void run({ kind: 'archive' }, targets) : null,
    trash: outside('trash').length > 0 ? () => void run({ kind: 'trash' }, targets) : null,
    star: { flagged, run: () => void run({ kind: 'flag', change: { flagged } }, targets) },
    toSort: {
      toSort,
      unsupported: toSortUnsupported,
      run: () => void run({ kind: 'flag', change: { to_sort: toSort } }, targets),
    },
    read: { seen, run: () => void run({ kind: 'flag', change: { seen } }, targets) },
    markUnread: () => void run({ kind: 'flag', change: { seen: false } }, targets),
    moveChoices:
      folders?.filter(
        (folder) =>
          movableInto(folder) && !(sources.length === 1 && source !== undefined && isSameFolder(folder.path, source)),
      ) ?? null,
    move: (folder) => void run({ kind: 'move', folder }, targets),
    otherAccounts: others.length > 0 ? others : null,
    moveToAccount: (to, folder, removal) => void run({ kind: 'moveToAccount', account: to, folder, removal }, targets),
  }
}

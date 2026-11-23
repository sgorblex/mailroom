import { useEffect } from 'react'

import { useAccountKeywords } from '@/accounts/accounts-context.ts'
import type { AccountId } from '@/api/accounts.ts'
import type { MailFolder, MoveRemoval } from '@/api/mail.ts'

import { isSameFolder, loadedFolders, roleFolder, useTriage, type Action, type Target } from './triage-context.ts'

/** The actions the toolbars and the shortcuts offer for the current targets; `null` is not offered. */
export interface TriageActions {
  targets: Target[]
  /**
   * How many messages they stand for when every one the list or search matches is selected:
   * a flag change, a move or a trash then asks to confirm that count and acts on them all.
   */
  matching: number | null
  archive: (() => void) | null
  /** Report spam: to the Junk folder, where every target's account has one. */
  junk: (() => void) | null
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
  /**
   * With every matching message selected, the loaded rows do not tell which way a flag change
   * goes: star, To sort and read then set, and these clear (`null` otherwise).
   */
  unstar: (() => void) | null
  markSorted: (() => void) | null
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
  matching: null,
  archive: null,
  junk: null,
  trash: null,
  star: null,
  toSort: null,
  read: null,
  markUnread: null,
  unstar: null,
  markSorted: null,
  moveChoices: null,
  move: () => undefined,
  otherAccounts: null,
  moveToAccount: () => undefined,
}

/** Where a message may be moved to in an account: a selectable folder other than Drafts and Sent. */
export function movableInto(folder: MailFolder): boolean {
  return folder.selectable && folder.role !== 'drafts' && folder.role !== 'sent'
}

/**
 * The actions on `of`, by default what the keys act on (the selection, else the open message,
 * or every message matching when they are selected).
 */
export function useTriageActions(of?: Target[]): TriageActions {
  const triage = useTriage()
  const { flagsOf, loadFolders, setBulk } = triage
  const targets = of ?? triage.targets
  const matching = of === undefined && triage.selection.size > 0 ? triage.matching : null
  const run = (action: Action, on: Target[]): Promise<void> => {
    if (matching !== null && (action.kind === 'flag' || action.kind === 'move' || action.kind === 'trash')) {
      setBulk(action)
      return Promise.resolve()
    }
    return triage.run(action, on)
  }
  const keywordsOf = useAccountKeywords()
  const accounts = [...new Set(targets.map((target) => target.account_id))]
  const accountsKey = JSON.stringify(accounts)

  useEffect(() => {
    for (const account of JSON.parse(accountsKey) as AccountId[]) {
      loadFolders(account, false)
    }
  }, [accountsKey, loadFolders])

  const foldersOf = (account: AccountId): MailFolder[] | undefined => loadedFolders(triage.folders[account])
  const outside = (role: 'archive' | 'junk' | 'trash'): Target[] =>
    targets.filter((target) => {
      const where = roleFolder(foldersOf(target.account_id), role)?.path
      return where === undefined || !isSameFolder(target.folder, where)
    })

  if (targets.length === 0) {
    return NO_TRIAGE_ACTIONS
  }

  const canMoveTo = (role: 'archive' | 'junk'): boolean =>
    accounts.every((account) => roleFolder(foldersOf(account), role) !== undefined) && outside(role).length > 0
  const both = matching !== null
  const flagged = both || !targets.every((target) => flagsOf(target).flagged)
  const toSort = both || !targets.every((target) => flagsOf(target).to_sort)
  const toSortUnsupported = accounts.some((each) => keywordsOf(each) === false)
  const seen = both || targets.some((target) => !flagsOf(target).seen)
  const [account] = accounts
  const folders = accounts.length === 1 && account !== undefined ? foldersOf(account) : undefined
  const sources = [...new Set(targets.map((target) => target.folder))]
  const others = triage.accounts.filter((each) => !accounts.includes(each))
  const [source] = sources
  return {
    targets,
    matching: matching?.total ?? null,
    archive: canMoveTo('archive') ? () => void run({ kind: 'archive' }, targets) : null,
    junk: canMoveTo('junk') ? () => void run({ kind: 'junk' }, targets) : null,
    trash: outside('trash').length > 0 ? () => void run({ kind: 'trash' }, targets) : null,
    star: { flagged, run: () => void run({ kind: 'flag', change: { flagged } }, targets) },
    toSort: {
      toSort,
      unsupported: toSortUnsupported,
      run: () => void run({ kind: 'flag', change: { to_sort: toSort } }, targets),
    },
    read: { seen, run: () => void run({ kind: 'flag', change: { seen } }, targets) },
    markUnread: () => void run({ kind: 'flag', change: { seen: false } }, targets),
    unstar: both ? () => void run({ kind: 'flag', change: { flagged: false } }, targets) : null,
    markSorted: both ? () => void run({ kind: 'flag', change: { to_sort: false } }, targets) : null,
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

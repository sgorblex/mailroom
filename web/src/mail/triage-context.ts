import { createContext, useContext, useEffect } from 'react'
import { useQuery, type QueryObserverResult } from '@tanstack/react-query'

import type { AccountId } from '@/api/accounts.ts'
import type { FlagChange, FolderRole, MailAddress, MailFolder, MatchScope, MessageItem, MoveRemoval } from '@/api/mail.ts'

import { foldersQuery } from './queries.ts'

export interface Flags {
  seen: boolean
  flagged: boolean
  to_sort: boolean
}

/**
 * What an action needs of a message: where it is, and its flags as the list or view read them;
 * its sender, where known, for the filter Report spam offers.
 */
export type Target = Pick<MessageItem, 'ref' | 'account_id' | 'folder'> & { flags: Flags; from?: MailAddress | null }

export type Action =
  | { kind: 'archive' }
  | { kind: 'junk' }
  | { kind: 'trash' }
  | { kind: 'move'; folder: MailFolder }
  | { kind: 'moveToAccount'; account: AccountId; folder: MailFolder; removal: MoveRemoval }
  | { kind: 'flag'; change: FlagChange }

/** Every message the list or search matches is selected, not only the loaded rows: `total` of them. */
export interface Matching {
  scope: MatchScope
  total: number
}

/** What a bulk action does to every matching message: a flag change, a move or a trash. */
export type MatchingAction = Extract<Action, { kind: 'flag' | 'move' | 'trash' }>

/** A folder of one of the user's accounts: where a drop moves messages to. */
export interface Destination {
  account: AccountId
  folder: MailFolder
}

export interface Failure {
  account: AccountId
  folder: string
  code: string
  /** How many a move to another account copied but could not remove here: they are in both. */
  inBoth?: number
}

/** One failure line per folder and code. */
export function failureKey(failure: Failure): string {
  return JSON.stringify([failure.account, failure.folder, failure.code, failure.inBoth ?? 0])
}

/**
 * How to take back an action that succeeded: move each source folder's messages back from where
 * they landed (`refs`, the new ones), or set back the flags it changed, per message.
 */
export type Undo =
  | { kind: 'move'; groups: { account: AccountId; folder: string; refs: string[] }[] }
  | { kind: 'flag'; accounts: AccountId[]; restores: { change: FlagChange; refs: string[] }[]; before: [string, Flags][] }

/** A sender just reported as spam: the mail screen offers a filter sending their mail to Junk. */
export interface SpamOffer {
  account: AccountId
  address: string
}

/** What the last action did, for the toolbar to word. */
export type Outcome =
  | { kind: 'done'; action: Action; count: number; failures: Failure[] }
  | { kind: 'refused'; action: Action; error: unknown }

export type FolderState =
  | { kind: 'loading' }
  | { kind: 'failed'; error: unknown }
  | { kind: 'loaded'; value: MailFolder[] }

export interface Triage {
  /** The user's accounts, for a move to another of them. */
  accounts: readonly AccountId[]
  folders: Readonly<Record<string, FolderState>>
  /** Loads an account's folders; `fresh` asks again even when they are there (or failed). */
  loadFolders: (account: AccountId, fresh: boolean) => void
  /** A message's flags, with what this screen changed since they were read. */
  flagsOf: (target: Target) => Flags
  /** Refs moved away since the list was read: their rows are gone. */
  removed: ReadonlySet<string>
  selection: ReadonlyMap<string, Target>
  /** Changing the selection, or clearing it, ends this. */
  select: (targets: Target[], selected: boolean) => void
  clearSelection: () => void
  /** Every message the list or search matches is selected (`selectMatching`), or `null`. */
  matching: Matching | null
  selectMatching: (matching: Matching) => void
  /** The bulk action asking for its count to be confirmed, or running, on `matching`. */
  bulk: MatchingAction | null
  setBulk: (action: MatchingAction | null) => void
  /** A bulk action ended, having touched `accounts`: the list and their folders are read again, the selection cleared. */
  bulkDone: (accounts: AccountId[]) => void
  /** The open message, once its view has loaded. */
  open: Target | null
  setOpen: (target: Target | null) => void
  /** The list tells the rows it shows, in order, for moving through them by keyboard. */
  setVisible: (rows: Target[]) => void
  visibleRows: () => Target[]
  /** What the toolbar acts on: the selection, else the open message. */
  targets: Target[]
  busy: boolean
  outcome: Outcome | null
  dismissOutcome: () => void
  /**
   * The list `source` read `rows` again: their flags as read now replace what this screen changed,
   * and the open message's (whose view was read before) when it is one of them.
   */
  refreshed: (source: string, rows: readonly Target[]) => void
  /** Folders were marked read: their rows drop what this screen marked unread, and the list is read again. */
  folderMarkedRead: (account: AccountId, folders: readonly string[]) => void
  /** The filter offered after a report of spam, until dismissed, undone or another report replaces it. */
  spamOffer: SpamOffer | null
  dismissSpamOffer: () => void
  /** Runs an action on `targets`; `silent` (marking read on open) neither waits for nor reports anything. */
  run: (action: Action, targets: Target[], options?: { silent?: boolean }) => Promise<void>
  picker: boolean
  setPicker: (open: boolean) => void
  /** The picker of a move to another account; opening one picker closes the other. */
  accountPicker: boolean
  /** Where a drop opened the account picker: on that account's folders, that folder first. */
  accountPickerInto: Destination | null
  setAccountPicker: (open: boolean, into?: Destination) => void
}

export const TriageContext = createContext<Triage | null>(null)

export function useTriage(): Triage {
  const triage = useContext(TriageContext)
  if (triage === null) {
    throw new Error('useTriage outside a TriageProvider')
  }
  return triage
}

/** Folder paths compare as IMAP does: INBOX in any case is the inbox. */
export function isSameFolder(a: string, b: string): boolean {
  return a === b || (a.toUpperCase() === 'INBOX' && b.toUpperCase() === 'INBOX')
}

/** The account's folder with `role` that can hold mail: a `\Noselect` one named for a role cannot. */
export function roleFolder(folders: readonly MailFolder[] | undefined, role: FolderRole): MailFolder | undefined {
  return folders?.find((folder) => folder.role === role && folder.selectable)
}

export function loadedFolders(state: FolderState | undefined): MailFolder[] | undefined {
  return state?.kind === 'loaded' ? state.value : undefined
}

const LOADING: FolderState = { kind: 'loading' }
const loadedStates = new WeakMap<MailFolder[], FolderState>()
const failedStates = new WeakMap<Error, FolderState>()

/**
 * The folders query's result as the screen shows it, the same object while it is unchanged: a
 * reload keeps showing the folders until it answers, a retry after a failure shows them loading.
 */
export function folderState({ status, fetchStatus, data, error }: QueryObserverResult<MailFolder[]>): FolderState {
  if (status === 'success') {
    let state = loadedStates.get(data)
    if (state === undefined) {
      state = { kind: 'loaded', value: data }
      loadedStates.set(data, state)
    }
    return state
  }
  if (status === 'error' && fetchStatus === 'idle') {
    let state = failedStates.get(error)
    if (state === undefined) {
      state = { kind: 'failed', error }
      failedStates.set(error, state)
    }
    return state
  }
  return LOADING
}

/** An account's folders, kept in the shared store; `fresh` loads them again on mount (the sidebar's counts). */
export function useAccountFolders(account: AccountId, fresh = false): { state: FolderState; retry: () => void } {
  const { loadFolders } = useTriage()
  const query = useQuery({ ...foldersQuery(account), refetchOnMount: fresh ? 'always' : false, retryOnMount: fresh })
  useEffect(() => {
    loadFolders(account, false)
  }, [account, loadFolders])
  return {
    state: folderState(query),
    retry: () => {
      void query.refetch()
    },
  }
}

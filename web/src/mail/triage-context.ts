import { createContext, useContext, useEffect } from 'react'

import type { AccountId } from '@/api/accounts.ts'
import type { FlagChange, FolderRole, MailFolder, MatchScope, MessageItem, MoveRemoval } from '@/api/mail.ts'

export interface Flags {
  seen: boolean
  flagged: boolean
  to_sort: boolean
}

/** What an action needs of a message: where it is, and its flags as the list or view read them. */
export type Target = Pick<MessageItem, 'ref' | 'account_id' | 'folder'> & { flags: Flags }

export type Action =
  | { kind: 'archive' }
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
  /** Counts the lists read again after an undo or a folder marked read: the list shown reads itself again when it changes. */
  reloads: number
  /** Folders were marked read: their rows drop what this screen marked unread, and the list is read again. */
  folderMarkedRead: (account: AccountId, folders: readonly string[]) => void
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

/** An account's folders from the shared store; `fresh` loads them again on mount (the sidebar's counts). */
export function useAccountFolders(account: AccountId, fresh = false): { state: FolderState; retry: () => void } {
  const { folders, loadFolders } = useTriage()
  useEffect(() => {
    loadFolders(account, fresh)
  }, [account, fresh, loadFolders])
  return {
    state: folders[account] ?? { kind: 'loading' },
    retry: () => {
      loadFolders(account, true)
    },
  }
}

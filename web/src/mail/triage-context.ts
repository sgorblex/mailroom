import { createContext, useContext, useEffect } from 'react'

import type { FlagChange, FolderRole, MailFolder, MessageItem, MoveRemoval } from '@/api/mail.ts'

export interface Flags {
  seen: boolean
  flagged: boolean
}

/** What an action needs of a message: where it is, and its flags as the list or view read them. */
export type Target = Pick<MessageItem, 'ref' | 'account_id' | 'folder'> & { flags: Flags }

export type Action =
  | { kind: 'archive' }
  | { kind: 'trash' }
  | { kind: 'move'; folder: MailFolder }
  | { kind: 'moveToAccount'; account: string; folder: MailFolder; removal: MoveRemoval }
  | { kind: 'flag'; change: FlagChange }

export interface Failure {
  account: string
  folder: string
  code: string
  /** How many a move to another account copied but could not remove here: they are in both. */
  inBoth?: number
}

/** One failure line per folder and code. */
export function failureKey(failure: Failure): string {
  return JSON.stringify([failure.account, failure.folder, failure.code, failure.inBoth ?? 0])
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
  accounts: readonly string[]
  folders: Readonly<Record<string, FolderState>>
  /** Loads an account's folders; `fresh` asks again even when they are there (or failed). */
  loadFolders: (account: string, fresh: boolean) => void
  /** A message's flags, with what this screen changed since they were read. */
  flagsOf: (target: Target) => Flags
  /** Refs moved away since the list was read: their rows are gone. */
  removed: ReadonlySet<string>
  selection: ReadonlyMap<string, Target>
  select: (targets: Target[], selected: boolean) => void
  clearSelection: () => void
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
  /** Runs an action on `targets`; `silent` (marking read on open) neither waits for nor reports anything. */
  run: (action: Action, targets: Target[], options?: { silent?: boolean }) => Promise<void>
  picker: boolean
  setPicker: (open: boolean) => void
  /** The picker of a move to another account; opening one picker closes the other. */
  accountPicker: boolean
  setAccountPicker: (open: boolean) => void
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
export function useAccountFolders(account: string, fresh = false): { state: FolderState; retry: () => void } {
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

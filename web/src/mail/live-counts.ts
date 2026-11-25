import type { AccountId } from '@/api/accounts.ts'
import type { LiveEvent, MailFolder } from '@/api/mail.ts'

import { isSameFolder } from './triage-context.ts'

type FolderChanged = Extract<LiveEvent, { type: 'folder_changed' }>

/**
 * What a `folder_changed` event sets: one folder's counts, or the account's, all read again (its
 * folder reads from All Mail, whose counts the event does not carry); `null` when the account's
 * folders are not loaded or do not hold it.
 */
export type LiveCountReach = { kind: 'folder'; folder: MailFolder } | { kind: 'account' } | null

export function liveCountReach(folders: readonly MailFolder[] | undefined, event: FolderChanged): LiveCountReach {
  const changed = folders?.find((folder) => isSameFolder(folder.path, event.folder))
  if (changed === undefined) {
    return null
  }
  return changed.role === 'all' ? { kind: 'account' } : { kind: 'folder', folder: changed }
}

/**
 * When live counts last arrived, per folder and per account, on a clock of their own that ticks
 * once per arrival: a count the screen guesses from a request started at an earlier tick yields
 * to them, as they already hold that change.
 */
export interface LiveCountClock {
  tick: number
  folders: Map<string, number>
  accounts: Map<AccountId, number>
}

export function liveCountClock(): LiveCountClock {
  return { tick: 0, folders: new Map(), accounts: new Map() }
}

/** One key per folder, `INBOX` in any case being one folder (`isSameFolder`). */
function folderKey(account: AccountId, path: string): string {
  return `${String(account)}/${path.toUpperCase() === 'INBOX' ? 'INBOX' : path}`
}

/** A live count arrived for the folder `path`, or for every folder of the account when `null`. */
export function recordLiveCount(clock: LiveCountClock, account: AccountId, path: string | null): void {
  clock.tick += 1
  if (path === null) {
    clock.accounts.set(account, clock.tick)
  } else {
    clock.folders.set(folderKey(account, path), clock.tick)
  }
}

/** Whether a live count for the folder arrived after `since`, a tick read from `clock` earlier. */
export function isLiveCountNewer(clock: LiveCountClock, account: AccountId, path: string, since: number): boolean {
  const arrived = Math.max(clock.folders.get(folderKey(account, path)) ?? 0, clock.accounts.get(account) ?? 0)
  return arrived > since
}

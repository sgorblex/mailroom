import type { AccountId } from '@/api/accounts.ts'

import { isWithin, renamedPath } from './folder-tree.ts'
import { storedPaths, storePaths } from './stored-paths.ts'
import { isSameFolder } from './triage-context.ts'

const STORAGE_PREFIX = 'mailroom.mail.subfolders.'

function stored(account: AccountId): string[] {
  return storedPaths(STORAGE_PREFIX, account) ?? []
}

/** Whether this browser lists `folder` of `account` with its subfolders. */
export function storedSubfolders(account: AccountId, folder: string): boolean {
  return stored(account).some((path) => isSameFolder(path, folder))
}

export function storeSubfolders(account: AccountId, folder: string, on: boolean): void {
  const others = stored(account).filter((path) => !isSameFolder(path, folder))
  storePaths(STORAGE_PREFIX, account, on ? [...others, folder] : others)
}

/** Carries the choices for `from` and the folders under it over to `to` once it is renamed. */
export function renameStoredSubfolders(account: AccountId, from: string, to: string, delimiter: string | null): void {
  storePaths(
    STORAGE_PREFIX,
    account,
    stored(account).map((path) => renamedPath(path, from, to, delimiter)),
  )
}

/** Forgets the choices for `folder` and the folders under it once it is deleted. */
export function forgetStoredSubfolders(account: AccountId, folder: string, delimiter: string | null): void {
  storePaths(
    STORAGE_PREFIX,
    account,
    stored(account).filter((path) => !isWithin(path, folder, delimiter)),
  )
}

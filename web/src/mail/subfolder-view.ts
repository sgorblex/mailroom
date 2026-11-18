import { isWithin, renamedPath } from './folder-tree.ts'
import { storedPaths, storePaths } from './stored-paths.ts'
import { isSameFolder } from './triage-context.ts'

const STORAGE_PREFIX = 'mailroom.mail.subfolders.'

function stored(account: string): string[] {
  return storedPaths(STORAGE_PREFIX, account) ?? []
}

/** Whether this browser lists `folder` of `account` with its subfolders. */
export function storedSubfolders(account: string, folder: string): boolean {
  return stored(account).some((path) => isSameFolder(path, folder))
}

export function storeSubfolders(account: string, folder: string, on: boolean): void {
  const others = stored(account).filter((path) => !isSameFolder(path, folder))
  storePaths(STORAGE_PREFIX, account, on ? [...others, folder] : others)
}

/** Carries the choices for `from` and the folders under it over to `to` once it is renamed. */
export function renameStoredSubfolders(account: string, from: string, to: string, delimiter: string | null): void {
  storePaths(
    STORAGE_PREFIX,
    account,
    stored(account).map((path) => renamedPath(path, from, to, delimiter)),
  )
}

/** Forgets the choices for `folder` and the folders under it once it is deleted. */
export function forgetStoredSubfolders(account: string, folder: string, delimiter: string | null): void {
  storePaths(
    STORAGE_PREFIX,
    account,
    stored(account).filter((path) => !isWithin(path, folder, delimiter)),
  )
}

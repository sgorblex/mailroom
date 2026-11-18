import type { AccountId } from '@/api/accounts.ts'

/** The folder paths this browser keeps under `prefix` for `account`; `null` when it keeps none. */
export function storedPaths(prefix: string, account: AccountId): string[] | null {
  try {
    const raw = window.localStorage.getItem(prefix + String(account))
    const parsed: unknown = raw === null ? null : JSON.parse(raw)
    return Array.isArray(parsed) && parsed.every((path) => typeof path === 'string') ? parsed : null
  } catch {
    return null
  }
}

export function storePaths(prefix: string, account: AccountId, paths: readonly string[]): void {
  try {
    window.localStorage.setItem(prefix + String(account), JSON.stringify(paths))
  } catch {
    // Storage refused (a private window): the choice is forgotten on reload, nothing else.
  }
}

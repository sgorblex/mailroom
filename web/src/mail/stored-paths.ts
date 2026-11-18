/** The folder paths this browser keeps under `prefix` for `account`; `null` when it keeps none. */
export function storedPaths(prefix: string, account: string): string[] | null {
  try {
    const raw = window.localStorage.getItem(prefix + account)
    const parsed: unknown = raw === null ? null : JSON.parse(raw)
    return Array.isArray(parsed) && parsed.every((path) => typeof path === 'string') ? parsed : null
  } catch {
    return null
  }
}

export function storePaths(prefix: string, account: string, paths: readonly string[]): void {
  try {
    window.localStorage.setItem(prefix + account, JSON.stringify(paths))
  } catch {
    // Storage refused (a private window): the choice is forgotten on reload, nothing else.
  }
}

/** Whether this browser can write dates in `timeZone`. */
function knownTimeZone(timeZone: string): boolean {
  try {
    new Intl.DateTimeFormat(undefined, { timeZone })
    return true
  } catch {
    return false
  }
}

/** The zone this browser is in, as an IANA name. */
export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone
}

/**
 * The zone to write dates in for a user who saved `saved`: that zone, else (none saved, or one this
 * browser does not know) `undefined`, which `Intl` reads as the browser's.
 */
export function writableTimeZone(saved: string | null): string | undefined {
  return saved !== null && knownTimeZone(saved) ? saved : undefined
}

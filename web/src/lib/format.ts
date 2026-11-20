/** `iso` in the reader's language as `options` write it; text that is no date shows as it is. */
function formatted(language: string, iso: string, options: Intl.DateTimeFormatOptions): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return iso
  }
  return new Intl.DateTimeFormat(language, options).format(date)
}

/**
 * A date and time in the reader's language and `timeZone` (the browser's when `undefined`); text
 * that is no date shows as it is.
 */
export function formatDateTime(language: string, iso: string | null, timeZone: string | undefined): string {
  return iso === null ? '' : formatted(language, iso, { dateStyle: 'medium', timeStyle: 'short', timeZone })
}

/** The day alone, as `formatDateTime` writes it. */
export function formatDay(language: string, iso: string, timeZone: string | undefined): string {
  return formatted(language, iso, { dateStyle: 'medium', timeZone })
}

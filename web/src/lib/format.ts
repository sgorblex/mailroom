/**
 * A date and time in the reader's language and `timeZone` (the browser's when `undefined`); text
 * that is no date shows as it is.
 */
export function formatDateTime(language: string, iso: string | null, timeZone: string | undefined): string {
  if (iso === null) {
    return ''
  }
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return iso
  }
  return new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeStyle: 'short', timeZone }).format(date)
}

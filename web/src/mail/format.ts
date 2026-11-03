import type { MailAddress } from '@/api/mail.ts'

/** A sender or recipient as a person reads it: the name when there is one. */
export function addressLabel(address: MailAddress): string {
  return address.name === '' ? address.address : address.name
}

/** `Name <address>`, or the bare address. */
export function addressFull(address: MailAddress): string {
  return address.name === '' ? address.address : `${address.name} <${address.address}>`
}

/** A size in bytes, in the reader's language and the unit that keeps it short. */
export function formatSize(language: string, bytes: number): string {
  const units = ['byte', 'kilobyte', 'megabyte', 'gigabyte'] as const
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return new Intl.NumberFormat(language, {
    style: 'unit',
    unit: units[unit],
    unitDisplay: 'short',
    maximumFractionDigits: unit === 0 ? 0 : 1,
  }).format(value)
}

/** A list row's date: the time for today, else the date; text that is no date shows as it is. */
export function formatListDate(language: string, iso: string | null, now: Date = new Date()): string {
  if (iso === null) {
    return ''
  }
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return iso
  }
  const today = date.toDateString() === now.toDateString()
  return new Intl.DateTimeFormat(
    language,
    today ? { timeStyle: 'short' } : { dateStyle: 'medium' },
  ).format(date)
}

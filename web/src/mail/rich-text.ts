/** The image types a message shows in place, as the server checks them. */
export const SHOWN_IMAGES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp'])

/** The links outgoing HTML keeps. */
export const SAFE_LINK = /^(https?:|mailto:)/i

/** A link as typed: a web or mail address, `null` for anything else. */
export function linkTarget(typed: string): string | null {
  const value = typed.trim()
  if (value === '') {
    return null
  }
  if (SAFE_LINK.test(value)) {
    return value
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(value)) {
    return null
  }
  return value.includes('@') && !value.includes('/') ? `mailto:${value}` : `https://${value}`
}

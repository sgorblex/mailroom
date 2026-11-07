import type { MailAddress } from '@/api/mail.ts'

const MAX_PER_FIELD = 50

/** One `@`, printable ASCII on both sides (a punycode domain is): the server's test, said early. */
const DELIVERABLE = /^[!-~]+@[!-~]+$/
const NOT_IN_AN_ADDRESS = /[<>,;"\s]/

export function isDeliverable(address: string): boolean {
  return DELIVERABLE.test(address) && address.indexOf('@') === address.lastIndexOf('@') && !NOT_IN_AN_ADDRESS.test(address)
}

/** Splits on `,` and `;` outside quotes and angle brackets. */
function entries(text: string): string[] {
  const found: string[] = []
  let current = ''
  let quoted = false
  let bracket = false
  for (const char of text) {
    if (char === '"') {
      quoted = !quoted
    } else if (char === '<') {
      bracket = true
    } else if (char === '>') {
      bracket = false
    }
    if ((char === ',' || char === ';' || char === '\n') && !quoted && !bracket) {
      found.push(current)
      current = ''
    } else {
      current += char
    }
  }
  found.push(current)
  return found.map((entry) => entry.trim()).filter((entry) => entry !== '')
}

function parseEntry(entry: string): MailAddress | null {
  const named = /^(.*)<([^<>]*)>$/.exec(entry)
  const address = (named === null ? entry : (named[2] ?? '')).trim()
  if (!isDeliverable(address)) {
    return null
  }
  const name = (named?.[1] ?? '').trim().replace(/^"(.*)"$/, '$1').trim()
  return { name, address }
}

/** What the user typed in a recipient field: the addresses in it, and what is not one. */
export function parseRecipients(text: string): { addresses: MailAddress[]; invalid: string[] } {
  const addresses: MailAddress[] = []
  const invalid: string[] = []
  for (const entry of entries(text)) {
    const parsed = parseEntry(entry)
    if (parsed === null) {
      invalid.push(entry)
    } else {
      addresses.push(parsed)
    }
  }
  return { addresses, invalid }
}

/** `list` with `more` added, each address once; `over` is what the field's limit left out. */
export function withRecipients(list: MailAddress[], more: MailAddress[]): { list: MailAddress[]; over: MailAddress[] } {
  const seen = new Set(list.map((each) => each.address.toLowerCase()))
  const added = more.filter((each) => {
    const key = each.address.toLowerCase()
    if (seen.has(key)) {
      return false
    }
    seen.add(key)
    return true
  })
  const merged = [...list, ...added]
  return { list: merged.slice(0, MAX_PER_FIELD), over: merged.slice(MAX_PER_FIELD) }
}

/** The text typed in a field added to its recipients: what cannot be added stays typed. */
export function commitTyped(list: MailAddress[], text: string): { list: MailAddress[]; text: string; invalid: boolean } {
  const { addresses, invalid } = parseRecipients(text)
  const { list: merged, over } = withRecipients(list, addresses)
  const left = [...invalid, ...over.map((each) => each.address)]
  return { list: merged, text: left.join(', '), invalid: left.length > 0 }
}

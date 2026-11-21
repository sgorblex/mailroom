import type { Account, IdentitySignature, Identity } from '@/api/accounts.ts'

/** The attribute of the block that holds the signature in a message's HTML, as the server keeps it. */
export const SIGNATURE_MARK = 'data-mailroom-signature'
/** What a signature follows in text: the usual delimiter line. */
const TEXT_MARK = '-- \n'

/** The account's default identity: the one marked so, else the first. */
export function defaultIdentity(identities: Identity[]): Identity | undefined {
  return identities.find((identity) => identity.default === true) ?? identities[0]
}

/** The address a message goes from: `from`, else the account's default. */
export function fromAddress(account: Account | undefined, from: string | null): string | null {
  return from ?? defaultIdentity(account?.identities ?? [])?.address ?? null
}

/** The signature of the identity `address`, `null` for none. */
export function signatureOf(signatures: IdentitySignature[], address: string | null): IdentitySignature | null {
  const found = signatures.find((each) => each.address.toLowerCase() === address?.toLowerCase())
  return found === undefined || found.html === '' ? null : found
}

/** The block a signature goes in, in the rich editor's HTML: empty for none. */
function signatureBlock(signature: IdentitySignature | null): string {
  return `<div ${SIGNATURE_MARK}="">${signature?.html ?? ''}</div>`
}

/** A new message's HTML: room to write, then the signature, if there is one. */
export function newMessageHtml(signature: IdentitySignature | null): string {
  return signature === null ? '' : `<p></p>${signatureBlock(signature)}`
}

/** Where `block` stands whole in `text`, from a line's start to the end or a blank line; -1 if nowhere. */
function blockAt(text: string, block: string): number {
  for (let at = text.indexOf(block); at !== -1; at = text.indexOf(block, at + 1)) {
    const rest = text.slice(at + block.length)
    if ((at === 0 || text[at - 1] === '\n') && (rest === '' || rest.startsWith('\n\n'))) {
      return at
    }
  }
  return -1
}

/**
 * Plain text with the signature `before` swapped for `after`, where the text still holds
 * `before` as it was put there; otherwise as it is.
 */
export function swapTextSignature(text: string, before: IdentitySignature | null, after: IdentitySignature | null): string {
  if (before === null) {
    return text
  }
  const old = `${TEXT_MARK}${before.text}`
  const at = blockAt(text, old)
  if (at === -1) {
    return text
  }
  const head = text.slice(0, at)
  const tail = text.slice(at + old.length)
  if (after !== null) {
    return `${head}${TEXT_MARK}${after.text}${tail}`
  }
  return head.endsWith('\n\n') ? `${head.slice(0, -2)}${tail}` : `${head}${tail.replace(/^\n\n/, '')}`
}

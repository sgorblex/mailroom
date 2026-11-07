import type { PrefillMode } from '@/api/mail.ts'

/** What a composer starts from. */
export type ComposeSource =
  | { kind: 'new'; account: string }
  | { kind: PrefillMode; account: string; ref: string }
  | { kind: 'draft'; ref: string }
  /** A composer open before a reload: the draft saved under its `compose_id`, if any. */
  | { kind: 'recover'; account: string; composeId: string }

export interface ComposeSession {
  id: number
  composeId: string
  source: ComposeSource
  /** The `compose` search param it was opened from, if any. */
  param: string | null
}

/** The server's pattern for a `compose_id`, which an agent's draft fills otherwise than ours; a ref holds no dot. */
const KEY = /^([A-Za-z0-9_-]{1,64})\.(.+)$/

/**
 * The `compose` search param of an open composer. Not a ref, which changes with every save and
 * would be out of date just when a reload needs it. It names the account too: the URL's `account`
 * is the one shown, not necessarily the draft's.
 */
export function composeKey(composeId: string, account: string): string {
  return `${composeId}.${account}`
}

/** The `compose` search param: `new`, a `composeKey`, or the ref of a draft to open. */
export function sourceFromParam(value: string, account: string): ComposeSource {
  if (value === 'new') {
    return { kind: 'new', account }
  }
  const key = KEY.exec(value)
  if (key !== null) {
    return { kind: 'recover', account: key[2] ?? account, composeId: key[1] ?? '' }
  }
  return { kind: 'draft', ref: value }
}

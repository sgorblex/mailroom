import type { MailAddress, MessageView } from '@/api/mail.ts'

/** Above this many To and Cc addresses together, the view sums them up until asked. */
export const RECIPIENTS_SHOWN = 3

export type AuthenticationState = 'pass' | 'fail' | 'unconfirmed' | 'unverified'

/**
 * What the account's own authentication server said: `pass` only on a DMARC pass for the one
 * From; `fail` when a result failed; `unconfirmed` when it checked and neither; `unverified` when
 * no result of it can be believed.
 */
export function authenticationState(authentication: MessageView['authentication']): AuthenticationState {
  if (!authentication.verified) {
    return 'unverified'
  }
  if (authentication.dmarc_pass) {
    return 'pass'
  }
  const { spf, dkim, dmarc } = authentication
  return [spf, dkim, dmarc].includes('fail') ? 'fail' : 'unconfirmed'
}

/** One result as the results line shows it: its word, then the domain it was for, when known. */
export function resultText(result: string, domain: string | null): string {
  return domain === null ? result : `${result} (${domain})`
}

/**
 * How many To and Cc addresses there are, and whether `sentTo` (the account's identity it arrived
 * at) is one of them: `null` when there are few enough to show them all.
 */
export function recipientSummary(
  to: MailAddress[],
  cc: MailAddress[],
  sentTo: string | null,
): { you: boolean; others: number } | null {
  const all = [...to, ...cc]
  if (all.length <= RECIPIENTS_SHOWN) {
    return null
  }
  const own = sentTo?.toLowerCase()
  const others = all.filter((recipient) => recipient.address.toLowerCase() !== own).length
  return { you: others < all.length, others }
}

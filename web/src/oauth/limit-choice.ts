import type { AccountId } from '@/api/accounts.ts'
import type { AccountChoice, AppLimits } from '@/api/oauth.ts'

/** The ceilings offered, strictest first: `direct` allows what every account does. */
export const CEILINGS = ['off', 'drafts', 'approval'] as const
export type Ceiling = (typeof CEILINGS)[number]

export const NO_LIMITS: AppLimits = { account_ids: null, send_ceiling: null }

/** The groups whose tools a send ceiling limits. */
const SENDING = ['mail_draft', 'mail_send']

/** Whether one of `groups` writes or sends mail. */
export function anySends(groups: readonly string[]): boolean {
  return groups.some((name) => SENDING.includes(name))
}

/** The ceiling as it limits anything, `null` when it does not. */
export function ceilingShown(ceiling: AppLimits['send_ceiling']): Ceiling | null {
  return CEILINGS.find((each) => each === ceiling) ?? null
}

/** Whether the limits can be approved: no accounts at all cannot. */
export function limitsApprovable(limits: AppLimits): boolean {
  return limits.account_ids === null || limits.account_ids.length > 0
}

/** `chosen` with the account checked or not, in the order of `accounts`. */
export function toggledAccounts(
  accounts: readonly AccountChoice[],
  chosen: readonly AccountId[],
  id: AccountId,
  checked: boolean,
): AccountId[] {
  return accounts
    .map((account) => account.id)
    .filter((each) => (each === id ? checked : chosen.includes(each)))
}

import type { TFunction } from 'i18next'

import { accountErrorMessage, mailboxMessage } from '@/accounts/messages.ts'
import { ApiRequestError } from '@/api/http.ts'

/** A rule set's filters at most, as the API caps them (`too_many_filters`). */
export const MAX_FILTERS = 100

/** A coded refusal about filters or a mailbox, in the reader's language; `null` for a code it does not know. */
function codedMessage(t: TFunction, code: string | undefined, message: string): string | null {
  switch (code) {
    case 'invalid_spec':
      return t('filterErrors.invalidSpec', { message })
    case 'invalid_rule_set':
      return t('filterErrors.invalidRuleSet')
    case 'run_filters_off':
      return t('filterErrors.runFiltersOff')
    case 'rule_set_disabled':
      return t('filterErrors.ruleSetDisabled')
    case 'run_pending':
      return t('filterErrors.runPending')
    case 'dry_run_timeout':
      return t('filterErrors.dryRunTimeout')
    case 'dry_run_busy':
      return t('filterErrors.dryRunBusy')
    case 'match_timeout':
      return t('filterErrors.matchTimeout')
    case 'too_many_filters':
      return t('filterErrors.tooManyFilters', { max: MAX_FILTERS })
    case 'invalid_order':
      return t('filterErrors.invalidOrder')
    case 'account_not_found':
      return t('filterErrors.accountNotFound')
    case 'interrupted':
      return t('filterErrors.interrupted')
    case 'run_failed':
      return t('filterErrors.runFailed', { message })
    case 'secrets_key_missing':
      return t('accountErrors.secretsKeyMissing')
  }
  return mailboxMessage(t, code, message)
}

/** The API's coded refusals about filters, in the reader's language; mailbox failures as accounts word them. */
export function filterErrorMessage(t: TFunction, failure: unknown, fallback: string): string {
  if (failure instanceof ApiRequestError) {
    const coded = codedMessage(t, failure.code, failure.message)
    if (coded !== null) {
      return coded
    }
    if (failure.status === 404) {
      return t('filterErrors.notFound')
    }
  }
  return accountErrorMessage(t, failure, fallback)
}

/** A run's recorded error (`"<code>: <message>"`), worded like the API's refusals; else as it is. */
export function runErrorMessage(t: TFunction, error: string): string {
  const at = error.indexOf(': ')
  if (at > 0) {
    const coded = codedMessage(t, error.slice(0, at), error.slice(at + 2))
    if (coded !== null) {
      return coded
    }
  }
  return error
}

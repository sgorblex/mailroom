import type { TFunction } from 'i18next'

import type { LoginTest } from '@/api/accounts.ts'
import { ApiRequestError, apiErrorMessage } from '@/api/http.ts'

/** The API's coded refusals about accounts, in the reader's language; anything else as it came. */
export function accountErrorMessage(t: TFunction, failure: unknown, fallback: string): string {
  if (failure instanceof ApiRequestError) {
    switch (failure.code) {
      case 'invalid_account':
      case 'account_name_taken':
        return t('accountErrors.invalidAccount')
      case 'accounts_changed':
        return t('accountErrors.accountsChanged')
      case 'accounts_busy':
        return t('accountErrors.accountsBusy')
      case 'handled_unsupported':
        return t('accountErrors.handledUnsupported')
      case 'handled_rule_sets':
        return t('accountErrors.handledRuleSets')
      case 'secrets_key_missing':
        return t('accountErrors.secretsKeyMissing')
    }
    const mailbox = mailboxMessage(t, failure.code, failure.message)
    if (mailbox !== null) {
      return mailbox
    }
  }
  return apiErrorMessage(failure, fallback)
}

/** What a login test found, worded for the reader. */
export function loginTestMessage(t: TFunction, result: LoginTest): string {
  if (result.ok) {
    return t('accounts.testOk')
  }
  return (
    mailboxMessage(t, result.code ?? undefined, result.message ?? undefined) ??
    t('accounts.testFailed', { message: result.message ?? '' })
  )
}

/** mailroom-core's mail error codes (`docs/requirements/mail-client.md`, *Errors*) and the mail routes' own, with their wording key. */
export const MAIL_CODES: Record<string, string> = {
  auth_failed: 'mailErrors.authFailed',
  unreachable: 'mailErrors.unreachable',
  timeout: 'mailErrors.timeout',
  mailbox_missing: 'mailErrors.mailboxMissing',
  unsupported: 'mailErrors.unsupported',
  busy: 'mailErrors.busy',
  stale_reference: 'mailErrors.staleReference',
  not_found: 'mailErrors.notFound',
  invalid_argument: 'mailErrors.invalidArgument',
  too_large: 'mailErrors.tooLarge',
  server_error: 'mailErrors.serverError',
  send_disabled: 'mailErrors.sendDisabled',
  rate_limited: 'mailErrors.rateLimited',
  recipient_refused: 'mailErrors.recipientRefused',
  delivery_unknown: 'mailErrors.deliveryUnknown',
  draft_not_editable: 'mailErrors.draftNotEditable',
  folder_exists: 'mailErrors.folderExists',
  folder_changed: 'mailErrors.folderChanged',
  folder_in_use: 'mailErrors.folderInUse',
  folder_is_handled: 'mailErrors.folderIsHandled',
  not_as_built: 'mailErrors.notAsBuilt',
}

/** The fixed detail mailroom-core's `unreachable` ends with when the server's TLS certificate did not verify (`CERTIFICATE_DETAIL`). */
const CERTIFICATE_DETAIL = 'Its TLS certificate did not verify.'

/**
 * A mailbox failure's code in the reader's language: the app's own account codes, then mailroom-core's; `null` for a code it
 * does not know. `message`, the API's own wording, adds the one reason it may carry: a certificate that did not verify.
 */
export function mailboxMessage(t: TFunction, code: string | undefined, message?: string): string | null {
  if (code === 'password_missing') {
    return t('accountErrors.passwordMissing')
  }
  const key = code === undefined ? undefined : MAIL_CODES[code]
  if (key === undefined) {
    return null
  }
  const worded = t(key)
  if (code === 'unreachable' && message?.endsWith(CERTIFICATE_DETAIL) === true) {
    return `${worded} ${t('mailErrors.certificateUnverified')}`
  }
  return worded
}

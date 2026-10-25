import type { TFunction } from 'i18next'

import type { LoginTest } from '@/api/accounts.ts'
import { ApiRequestError, apiErrorMessage } from '@/api/http.ts'

/** The API's coded refusals about accounts, in the reader's language; anything else as it came. */
export function accountErrorMessage(t: TFunction, failure: unknown, fallback: string): string {
  if (failure instanceof ApiRequestError) {
    switch (failure.code) {
      case 'account_exists':
        return t('accountErrors.accountExists')
      case 'invalid_account':
        return t('accountErrors.invalidAccount')
      case 'accounts_changed':
        return t('accountErrors.accountsChanged')
      case 'accounts_busy':
        return t('accountErrors.accountsBusy')
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
  return mailboxMessage(t, result.code ?? undefined, result.message ?? '') ?? t('accounts.testFailed', { message: result.message ?? '' })
}

export function mailboxMessage(t: TFunction, code: string | undefined, message: string): string | null {
  switch (code) {
    case 'login_failed':
      return t('accountErrors.loginFailed', { message })
    case 'unreachable':
    case 'timeout':
      return t('accountErrors.unreachable', { message })
    case 'tls_failed':
      return t('accountErrors.tlsFailed', { message })
    case 'starttls_unsupported':
      return t('accountErrors.starttlsUnsupported')
    case 'password_missing':
      return t('accountErrors.passwordMissing')
    case 'imap_error':
      return t('accounts.testFailed', { message })
    default:
      return null
  }
}

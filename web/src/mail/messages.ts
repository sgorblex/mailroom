import type { TFunction } from 'i18next'

import { accountErrorMessage, mailboxMessage } from '@/accounts/messages.ts'
import { ApiRequestError } from '@/api/http.ts'
import type { MailFolder } from '@/api/mail.ts'

/** The mailbox codes of the mail routes (`docs/requirements/mail-client.md`), with their wording key. */
const MAIL_CODES: Record<string, string> = {
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
}

/** `not_found` from the account routes (folders, a list, a search): the account is not there. */
export const ACCOUNT_NOT_FOUND = 'mailErrors.accountNotFound'

/**
 * A code of a mail route in the reader's language: the mailbox codes, then the app's own account
 * codes as Accounts words them (`password_missing`, `starttls_unsupported`,
 * `secrets_key_missing`); one it does not know reads as a server error. `notFound` words
 * `not_found` where it names an account rather than a message.
 */
export function mailCodeMessage(t: TFunction, code: string, notFound?: string): string {
  if (code === 'not_found' && notFound !== undefined) {
    return t(notFound)
  }
  const mailbox = MAIL_CODES[code]
  if (mailbox !== undefined) {
    return t(mailbox)
  }
  if (code === 'secrets_key_missing') {
    return t('accountErrors.secretsKeyMissing')
  }
  return mailboxMessage(t, code, '') ?? t('mailErrors.serverError')
}

/**
 * A refused mail request in the reader's language; `notFound` as for `mailCodeMessage`. Any
 * other code (the account ones) as Accounts words it; no code at all (a proxy's error page),
 * `fallback`.
 */
export function mailErrorMessage(t: TFunction, failure: unknown, fallback: string, notFound?: string): string {
  if (failure instanceof ApiRequestError) {
    const code = failure.code
    if (code === undefined) {
      return fallback
    }
    if (code in MAIL_CODES) {
      return mailCodeMessage(t, code, notFound)
    }
  }
  return accountErrorMessage(t, failure, fallback)
}

/** A folder as the sidebar and the move picker name it: its role's name, else its path. */
export function folderName(t: TFunction, folder: Pick<MailFolder, 'path' | 'role'>): string {
  return folder.role === null ? folder.path : t(`roles.${folder.role}`)
}

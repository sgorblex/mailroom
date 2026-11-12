import type { TFunction } from 'i18next'

import { MAIL_CODES, accountErrorMessage, mailboxMessage } from '@/accounts/messages.ts'
import { ApiRequestError } from '@/api/http.ts'
import type { MailFolder } from '@/api/mail.ts'

/** `not_found` from the account routes (folders, a list, a search): the account is not there. */
export const ACCOUNT_NOT_FOUND = 'mailErrors.accountNotFound'

/**
 * A code of a mail route in the reader's language, as Accounts words mailbox failures
 * (`mailboxMessage`: mailroom-core's codes and the app's `password_missing`,
 * `starttls_unsupported`), plus `secrets_key_missing`; one it does not know reads as a server
 * error. `notFound` words `not_found` where it names an account rather than a message; `message`, the API's wording, as
 * `mailboxMessage` reads it.
 */
export function mailCodeMessage(t: TFunction, code: string, notFound?: string, message?: string): string {
  if (code === 'not_found' && notFound !== undefined) {
    return t(notFound)
  }
  if (code === 'secrets_key_missing') {
    return t('accountErrors.secretsKeyMissing')
  }
  return mailboxMessage(t, code, message) ?? t('mailErrors.serverError')
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
      return mailCodeMessage(t, code, notFound, failure.message)
    }
  }
  return accountErrorMessage(t, failure, fallback)
}

/** A refused save or send: what is invalid there is one of the composer's limits, `limit` if it knows which. */
export function composeErrorMessage(t: TFunction, failure: unknown, fallback: string, limit?: string | null): string {
  if (failure instanceof ApiRequestError && failure.code === 'invalid_argument') {
    return limit ?? t('compose.invalidDraft')
  }
  return mailErrorMessage(t, failure, fallback)
}

/** The notes of a delivered send (`note_codes`), with their wording key. */
const NOTE_CODES: Record<string, string> = {
  draft_not_removed: 'compose.noteDraftNotRemoved',
  draft_kept: 'compose.noteDraftKept',
  original_not_marked: 'compose.noteOriginalNotMarked',
}

/** How a delivered send went, in one sentence or a few. */
export function sentMessage(t: TFunction, notice: { filed: boolean | null; rejected: string[]; note_codes: string[] }): string {
  const parts = [t('compose.sent')]
  if (notice.filed === false) {
    parts.push(t('compose.notFiled'))
  }
  if (notice.rejected.length > 0) {
    parts.push(t('compose.rejected', { recipients: notice.rejected.join(', ') }))
  }
  for (const code of notice.note_codes) {
    const note = NOTE_CODES[code]
    if (note !== undefined) {
      parts.push(t(note))
    }
  }
  return parts.join(' ')
}

/** A folder as the sidebar and the move picker name it: its role's name, else its path. */
export function folderName(t: TFunction, folder: Pick<MailFolder, 'path' | 'role'>): string {
  return folder.role === null ? folder.path : t(`roles.${folder.role}`)
}

import type { TFunction } from 'i18next'

import { ApiRequestError, apiErrorMessage } from '@/api/http.ts'
import { MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH } from '@/users/password.ts'

/** The API's coded refusals about users, in the reader's language; anything else as it came. */
export function userErrorMessage(t: TFunction, failure: unknown, fallback: string): string {
  if (failure instanceof ApiRequestError) {
    if (failure.status === 429) {
      return t('login.tooManyAttempts')
    }
    switch (failure.code) {
      case 'username_taken':
        return t('errors.usernameTaken')
      case 'username_invalid':
        return t('errors.usernameInvalid')
      case 'password_length':
        return t('errors.passwordLength', { min: MIN_PASSWORD_LENGTH, max: MAX_PASSWORD_LENGTH })
      case 'cannot_remove_self':
        return t('errors.cannotRemoveSelf')
      case 'cannot_reset_self':
        return t('errors.cannotResetSelf')
      case 'remover_gone':
        return t('errors.removerGone')
      case 'user_accounts_busy':
        return t('errors.userAccountsBusy')
      case 'wrong_password':
        return t('errors.wrongPassword')
    }
  }
  return apiErrorMessage(failure, fallback)
}

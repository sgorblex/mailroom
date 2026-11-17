import { createContext, useCallback, useContext } from 'react'
import { useTranslation } from 'react-i18next'

import type { Account, AccountId } from '@/api/accounts.ts'

/** The user's accounts as the screen loaded them, to name an account by its id; `null` until they are. */
export const AccountsContext = createContext<readonly Account[] | null>(null)

/** How the web names an account: its display name and colour. */
export type AccountLabel = Pick<Account, 'display_name' | 'colour'>

/**
 * Names an account by its id, from the accounts the screen loaded. An id none of them has (a rule
 * set or an audit record of an account that is gone) is a removed account, never shown as the id;
 * before the accounts are loaded, it has no name yet.
 */
export function useAccountLabel(): (id: AccountId) => AccountLabel {
  const accounts = useContext(AccountsContext)
  const { t } = useTranslation()
  return useCallback(
    (id: AccountId) => {
      if (accounts === null) {
        return { display_name: '', colour: null }
      }
      const account = accounts.find((each) => each.id === id)
      return account ?? { display_name: t('accounts.unknown'), colour: null }
    },
    [accounts, t],
  )
}

/** An account's name by its id, for text: a string, an option, an aria label. */
export function useAccountName(): (id: AccountId) => string {
  const label = useAccountLabel()
  return useCallback((id: AccountId) => label(id).display_name, [label])
}

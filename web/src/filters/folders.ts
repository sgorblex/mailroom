import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ALL_ACCOUNTS, listFolders, type RuleSetAccount } from '@/api/filters.ts'
import { accountErrorMessage } from '@/accounts/messages.ts'
import { FILTERS_NS } from './i18n.ts'

export type FoldersState =
  | { kind: 'loading' }
  | { kind: 'failed'; message: string }
  | { kind: 'loaded'; paths: string[][] }
  /** A rule set for all accounts: no one account's folders, so a path is typed. */
  | { kind: 'all-accounts' }

const ALL: FoldersState = { kind: 'all-accounts' }

/** The account's folders, as path segments, for the folder pickers; none listed for all accounts. */
export function useFolders(accountId: RuleSetAccount): FoldersState {
  const { t } = useTranslation(FILTERS_NS)
  const [state, setState] = useState<{ accountId: RuleSetAccount; folders: FoldersState }>({
    accountId,
    folders: { kind: 'loading' },
  })

  useEffect(() => {
    if (accountId === ALL_ACCOUNTS) {
      return
    }
    const controller = new AbortController()
    listFolders(accountId, controller.signal).then(
      (folders) => {
        setState({ accountId, folders: { kind: 'loaded', paths: folders.map((folder) => folder.path) } })
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          const message = accountErrorMessage(t, error, t('filters.foldersFailed'))
          setState({ accountId, folders: { kind: 'failed', message } })
        }
      },
    )
    return () => {
      controller.abort()
    }
  }, [accountId, t])

  if (accountId === ALL_ACCOUNTS) {
    return ALL
  }
  return state.accountId === accountId ? state.folders : { kind: 'loading' }
}

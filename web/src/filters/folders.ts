import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { listFolders } from '@/api/accounts.ts'
import { accountErrorMessage } from '@/accounts/messages.ts'
import { FILTERS_NS } from './i18n.ts'

export type FoldersState =
  | { kind: 'loading' }
  | { kind: 'failed'; message: string }
  | { kind: 'loaded'; paths: string[][] }

/** The account's folders, as path segments, for the folder pickers. */
export function useFolders(accountId: string): FoldersState {
  const { t } = useTranslation(FILTERS_NS)
  const [state, setState] = useState<{ accountId: string; folders: FoldersState }>({
    accountId,
    folders: { kind: 'loading' },
  })

  useEffect(() => {
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

  return state.accountId === accountId ? state.folders : { kind: 'loading' }
}

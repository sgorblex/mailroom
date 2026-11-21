import { useQueries } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { readIndexStatus, type IndexStatus } from '@/api/mail.ts'

import { MAIL_NS } from './i18n.ts'
import { indexProgress, type IndexScope } from './index-progress.ts'
import { INDEXING_POLL_MS, indexWaitingKey } from './queries.ts'

/**
 * A quiet line above the list while a folder it reads is not yet in the index (read live until it
 * is); nothing once each is, nor for a folder off the index or a status that cannot be read.
 */
export function IndexingNotice({ scope }: { scope: IndexScope }): ReactElement | null {
  const { t } = useTranslation(MAIL_NS)
  const statuses = useQueries({
    queries: scope.accounts.map((account) => ({
      queryKey: indexWaitingKey(account),
      queryFn: ({ signal }: { signal: AbortSignal }) => readIndexStatus(account, signal, true),
      retry: false,
      refetchInterval: (query: { state: { data?: IndexStatus } }) =>
        query.state.data !== undefined && indexProgress([query.state.data], scope) !== null ? INDEXING_POLL_MS : false,
    })),
  })
  const progress = indexProgress(
    statuses.flatMap((each) => (each.data === undefined ? [] : [each.data])),
    scope,
  )
  if (progress === null) {
    return null
  }
  return (
    <p className="text-xs text-muted-foreground">
      {progress.total === null
        ? t('list.indexing', { count: progress.indexed })
        : t('list.indexingOf', { indexed: progress.indexed, count: progress.total })}
    </p>
  )
}

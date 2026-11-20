import { useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from '@tanstack/react-router'

import type { Account } from '@/api/accounts.ts'
import { ApiRequestError, apiErrorMessage } from '@/api/http.ts'
import { unmuteThread, type MutedThread } from '@/api/mail.ts'
import { useTimeZone } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { formatDateTime } from '@/lib/format.ts'

import { FILTERS_NS } from './i18n.ts'

/**
 * An account's muted conversations, each to unmute; with Run filters off, that muting has no
 * effect until it is on. Nothing names a conversation but when it was muted: the API answers no
 * subject and no id.
 */
export function MutedThreads({
  account,
  threads,
  onChanged,
}: {
  account: Account
  threads: MutedThread[]
  /** A thread was unmuted, or found unmuted already: the list is to be loaded again. */
  onChanged: () => void
}): ReactElement {
  const { t, i18n } = useTranslation(FILTERS_NS)
  const timeZone = useTimeZone()
  const [pending, setPending] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  async function unmute(id: number): Promise<void> {
    setPending(true)
    setFailure(null)
    try {
      await unmuteThread(id)
      onChanged()
    } catch (error) {
      // A 404: unmuted elsewhere already, which is what the user asked for.
      if (error instanceof ApiRequestError && error.status === 404) {
        onChanged()
      } else {
        setFailure(apiErrorMessage(error, t('filters.muted.unmuteFailed')))
      }
    } finally {
      setPending(false)
    }
  }

  const headingId = `account-${String(account.id)}-muted`
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2">
      <h3 id={headingId} className="font-medium">
        {t('filters.muted.title')}
      </h3>
      <p className="text-sm text-muted-foreground">{t('filters.muted.intro')}</p>
      {account.run_filters !== true && (
        <p className="text-sm">
          {t('filters.muted.runFiltersOff')}{' '}
          <Link to="/accounts" className="underline underline-offset-4">
            {t('filters.goToAccounts')}
          </Link>
        </p>
      )}
      <ul
        aria-label={t('filters.muted.of', { account: account.display_name })}
        className="flex flex-col divide-y rounded-lg border"
      >
        {threads.map((thread) => {
          const when = formatDateTime(i18n.language, thread.created_at, timeZone)
          return (
            <li key={thread.id} className="flex items-center gap-3 p-3">
              <div className="mr-auto min-w-0">
                <p className="text-sm font-medium">{t('filters.muted.since', { when })}</p>
                <p className="text-xs text-muted-foreground">
                  {[
                    ...(thread.ids > 0 ? [t('filters.muted.ids', { count: thread.ids })] : []),
                    ...(thread.gmail ? [t('filters.muted.gmail')] : []),
                  ].join(' · ')}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                aria-label={t('filters.muted.unmuteThread', { when })}
                disabled={pending}
                onClick={() => void unmute(thread.id)}
              >
                {t('filters.muted.unmute')}
              </Button>
            </li>
          )
        })}
      </ul>
      {failure !== null && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
    </section>
  )
}

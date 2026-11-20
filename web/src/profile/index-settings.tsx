import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

import { listAccounts, type Account, type AccountId } from '@/api/accounts.ts'
import { apiErrorMessage } from '@/api/http.ts'
import { INDEX_MAX_AGE_DAYS, readIndexStatus, setIndexFolder, type IndexFolder } from '@/api/mail.ts'
import { useAuth, useTimeZone } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'
import { formatDateTime, formatDay } from '@/lib/format.ts'
import { ACCOUNTS_KEY, INDEX_KEY, INDEXING_POLL_MS, indexStatusKey, READ_ON_MOUNT } from '@/mail/queries.ts'
import { ACCOUNT_NOT_FOUND, mailCodeMessage, mailErrorMessage } from '@/mail/messages.ts'

type Outcome = { kind: 'idle' } | { kind: 'saved' } | { kind: 'failed'; message: string }

/**
 * The mail index's settings: how far back it keeps mail, and per account which folders it keeps,
 * each with its sync state and counts. A folder off the index is read live.
 */
export function IndexSettings(): ReactElement {
  const { t } = useTranslation()
  const accounts = useQuery({ queryKey: ACCOUNTS_KEY, queryFn: ({ signal }) => listAccounts(signal), ...READ_ON_MOUNT })
  return (
    <section aria-labelledby="mail-index" className="flex flex-col gap-3">
      <h2 id="mail-index" className="font-medium">
        {t('mailIndex.title')}
      </h2>
      <p className="text-sm text-muted-foreground">{t('mailIndex.intro')}</p>
      <AgeLimitForm />
      {accounts.isError && (
        <p role="alert" className="text-sm text-destructive">
          {apiErrorMessage(accounts.error, t('accounts.loadFailed'))}
        </p>
      )}
      {accounts.data?.length === 0 && <p className="text-sm text-muted-foreground">{t('accounts.none')}</p>}
      {accounts.data?.map((account) => (
        <AccountIndex key={account.id} account={account} />
      ))}
    </section>
  )
}

function AgeLimitForm(): ReactElement {
  const { t } = useTranslation()
  const auth = useAuth()
  const queryClient = useQueryClient()
  const saved = auth.status === 'signed-in' ? auth.user.index_max_age_days : null
  const [days, setDays] = useState(saved === null ? '' : String(saved))
  const [saving, setSaving] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' })

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setSaving(true)
    setOutcome({ kind: 'idle' })
    try {
      await auth.changeIndexMaxAge(days.trim() === '' ? null : Number(days))
      setOutcome({ kind: 'saved' })
      void queryClient.invalidateQueries({ queryKey: INDEX_KEY })
    } catch (failure) {
      setOutcome({ kind: 'failed', message: apiErrorMessage(failure, t('mailIndex.saveFailed')) })
    } finally {
      setSaving(false)
    }
  }

  return (
    <form className="flex flex-col gap-1" onSubmit={(event) => void submit(event)}>
      <label htmlFor="mail-index-days" className="text-sm font-medium">
        {t('mailIndex.maxAge')}
      </label>
      <Input
        id="mail-index-days"
        type="number"
        inputMode="numeric"
        min={1}
        max={INDEX_MAX_AGE_DAYS}
        step={1}
        value={days}
        aria-invalid={outcome.kind === 'failed'}
        aria-describedby={outcome.kind === 'failed' ? 'mail-index-days-hint mail-index-error' : 'mail-index-days-hint'}
        onChange={(event) => {
          setDays(event.target.value)
          setOutcome({ kind: 'idle' })
        }}
      />
      <p id="mail-index-days-hint" className="text-sm text-muted-foreground">
        {t('mailIndex.maxAgeHint')}
      </p>
      {outcome.kind === 'failed' && (
        <p id="mail-index-error" role="alert" className="text-sm text-destructive">
          {outcome.message}
        </p>
      )}
      {outcome.kind === 'saved' && (
        <p role="status" className="text-sm">
          {t('mailIndex.saved')}
        </p>
      )}
      <Button type="submit" className="mt-2" disabled={saving}>
        {t('mailIndex.save')}
      </Button>
    </form>
  )
}

/** What the index holds of `folder`, or why it holds nothing, in a line. */
function folderState(t: TFunction, language: string, timeZone: string | undefined, folder: IndexFolder): string {
  if (folder.indexed_as === 'live') {
    return t('mailIndex.live')
  }
  if (folder.indexed_as === 'label') {
    return folder.enabled ? t('mailIndex.label') : t('mailIndex.off')
  }
  if (!folder.enabled || folder.state === null) {
    return t('mailIndex.off')
  }
  const indexed = folder.indexed ?? 0
  switch (folder.state) {
    case 'pending':
      return t('mailIndex.pending')
    case 'syncing':
      return folder.total === null
        ? t('mailIndex.syncing', { count: indexed })
        : t('mailIndex.syncingOf', { count: indexed, total: folder.total })
    case 'failed':
      return t('mailIndex.failed', { reason: mailCodeMessage(t, folder.error ?? '') })
    case 'ready': {
      const parts = [t('mailIndex.ready', { count: indexed, unread: folder.unread ?? 0 })]
      if (folder.oldest_indexed !== null) {
        // A plain day, read as UTC midnight: shown in UTC, not shifted by the user's zone.
        parts.push(t('mailIndex.since', { day: formatDay(language, folder.oldest_indexed, 'UTC') }))
      }
      if (folder.last_synced_at !== null) {
        parts.push(t('mailIndex.synced', { when: formatDateTime(language, folder.last_synced_at, timeZone) }))
      }
      return parts.join(' · ')
    }
  }
}

function AccountIndex({ account }: { account: Account }): ReactElement {
  const { t, i18n } = useTranslation()
  const timeZone = useTimeZone()
  const queryClient = useQueryClient()
  const queryKey = indexStatusKey(account.id)
  const status = useQuery({
    queryKey,
    queryFn: ({ signal }) => readIndexStatus(account.id, signal),
    ...READ_ON_MOUNT,
    refetchInterval: (query) =>
      query.state.data?.folders.some((folder) => folder.state === 'pending' || folder.state === 'syncing') === true
        ? INDEXING_POLL_MS
        : false,
  })
  const [switching, setSwitching] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  async function toggle(id: AccountId, folder: string, enabled: boolean): Promise<void> {
    setSwitching(folder)
    setFailure(null)
    try {
      await setIndexFolder(id, folder, enabled)
    } catch (error) {
      setFailure(mailErrorMessage(t, error, t('mailIndex.switchFailed')))
    } finally {
      await queryClient.invalidateQueries({ queryKey })
      setSwitching(null)
    }
  }

  const headingId = `mail-index-${String(account.id)}`
  const data = status.data
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2">
      <h3 id={headingId} className="text-sm font-medium">
        {account.display_name}
      </h3>
      {status.isPending && <p className="text-sm text-muted-foreground">{t('mailIndex.loading')}</p>}
      {status.isError && (
        <p role="alert" className="text-sm text-destructive">
          {mailErrorMessage(t, status.error, t('mailIndex.loadFailed'), ACCOUNT_NOT_FOUND)}
        </p>
      )}
      {data !== undefined && (
        <>
          <p className="text-sm text-muted-foreground">
            {data.max_age_days === null
              ? t('mailIndex.everyMessage')
              : t('mailIndex.lastDays', { count: data.max_age_days })}
            {data.max_age_cap_days !== null && ` ${t('mailIndex.cap', { count: data.max_age_cap_days })}`}
            {data.gmail && ` ${t('mailIndex.gmail')}`}
          </p>
          <ul aria-label={t('mailIndex.folders', { account: account.display_name })} className="flex flex-col divide-y rounded-lg border">
            {data.folders.map((folder, position) => {
              const inputId = `${headingId}-${String(position)}`
              return (
                <li key={folder.path} className="flex items-start gap-3 p-3">
                  <input
                    id={inputId}
                    type="checkbox"
                    className="mt-1"
                    checked={folder.enabled}
                    disabled={folder.indexed_as !== 'folder' || switching !== null}
                    aria-describedby={`${inputId}-state`}
                    onChange={(event) => void toggle(account.id, folder.path, event.target.checked)}
                  />
                  <div className="min-w-0">
                    <label htmlFor={inputId} className="block truncate text-sm font-medium">
                      {folder.path}
                    </label>
                    <p id={`${inputId}-state`} className="text-xs text-muted-foreground">
                      {folderState(t, i18n.language, timeZone, folder)}
                    </p>
                  </div>
                </li>
              )
            })}
          </ul>
        </>
      )}
      {failure !== null && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
    </section>
  )
}

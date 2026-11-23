import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useMemo, useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

import { listAccounts, type Account } from '@/api/accounts.ts'
import { apiErrorMessage } from '@/api/http.ts'
import { INDEX_MAX_AGE_DAYS, readIndexStatus, setIndexFolder, type IndexFolder } from '@/api/mail.ts'
import { useAuth, useTimeZone } from '@/auth/auth-context.ts'
import { ConfirmRemoval } from '@/components/confirm-removal.tsx'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'
import { formatDateTime, formatDay } from '@/lib/format.ts'
import { ACCOUNTS_KEY, INDEX_KEY, INDEXING_POLL_MS, indexStatusKey, READ_ON_MOUNT } from '@/mail/queries.ts'
import { ACCOUNT_NOT_FOUND, mailCodeMessage, mailErrorMessage } from '@/mail/messages.ts'

import { filterTree, indexTree, isSwitchable, matchingSwitchable, switchableUnder, tickState, type IndexNode } from './index-tree.ts'

type Outcome = { kind: 'idle' } | { kind: 'saved' } | { kind: 'failed'; message: string }

/**
 * The mail index's settings: how far back it keeps mail, and per account which folders it keeps,
 * as a tree with each folder's sync state and counts, narrowed by a filter shared by the accounts.
 * A folder off the index is read live.
 */
export function IndexSettings(): ReactElement {
  const { t } = useTranslation()
  const accounts = useQuery({ queryKey: ACCOUNTS_KEY, queryFn: ({ signal }) => listAccounts(signal), ...READ_ON_MOUNT })
  const [query, setQuery] = useState('')
  return (
    <section aria-labelledby="mail-index" className="flex flex-col gap-3">
      <h3 id="mail-index" className="font-medium">
        {t('mailIndex.title')}
      </h3>
      <p className="text-sm text-muted-foreground">{t('mailIndex.intro')}</p>
      <AgeLimitForm />
      {accounts.isError && (
        <p role="alert" className="text-sm text-destructive">
          {apiErrorMessage(accounts.error, t('accounts.loadFailed'))}
        </p>
      )}
      {accounts.data?.length === 0 && <p className="text-sm text-muted-foreground">{t('accounts.none')}</p>}
      {accounts.data !== undefined && accounts.data.length > 0 && (
        <div className="flex flex-col gap-1">
          <label htmlFor="mail-index-filter" className="text-sm font-medium">
            {t('mailIndex.filter')}
          </label>
          <Input
            id="mail-index-filter"
            type="search"
            value={query}
            aria-describedby="mail-index-filter-hint"
            onChange={(event) => {
              setQuery(event.target.value)
            }}
          />
          <p id="mail-index-filter-hint" className="text-sm text-muted-foreground">
            {t('mailIndex.filterHint')}
          </p>
        </div>
      )}
      {accounts.data?.map((account) => (
        <AccountIndex key={account.id} account={account} query={query} />
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

function AccountIndex({ account, query }: { account: Account; query: string }): ReactElement {
  const { t } = useTranslation()
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
  /** How far a batch of switches has come; `null` when none is running. */
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  /** The folders waiting for the user to confirm they stop being indexed. */
  const [confirming, setConfirming] = useState<readonly IndexFolder[] | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const folders = status.data?.folders
  const shown = useMemo(() => (folders === undefined ? [] : filterTree(indexTree(folders), query)), [folders, query])
  const switching = progress !== null
  const busy = switching || confirming !== null

  /**
   * Switches those of `targets` not yet `enabled` to it; switching more than one folder off, which
   * drops their indexed mail, asks first.
   */
  function requestSwitch(targets: readonly IndexFolder[], enabled: boolean): void {
    const changing = targets.filter((folder) => folder.enabled !== enabled)
    if (changing.length === 0) {
      return
    }
    setFailure(null)
    if (!enabled && changing.length > 1) {
      setConfirming(changing)
      return
    }
    void switchAll(changing, enabled)
  }

  /** Switches each of `changing` to `enabled` in turn, one call each, stopping at the first refused. */
  async function switchAll(changing: readonly IndexFolder[], enabled: boolean): Promise<void> {
    setConfirming(null)
    setProgress({ done: 0, total: changing.length })
    try {
      for (const [done, folder] of changing.entries()) {
        await setIndexFolder(account.id, folder.path, enabled)
        setProgress({ done: done + 1, total: changing.length })
      }
    } catch (error) {
      setFailure(mailErrorMessage(t, error, t('mailIndex.switchFailed')))
    } finally {
      await queryClient.invalidateQueries({ queryKey })
      setProgress(null)
    }
  }

  const headingId = `mail-index-${String(account.id)}`
  const data = status.data
  const matching = matchingSwitchable(shown, query)
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <h4 id={headingId} className="mr-auto text-sm font-medium">
          {account.display_name}
        </h4>
        {data !== undefined && (
          <>
            <Button
              type="button"
              variant="outline"
              size="xs"
              aria-label={t('mailIndex.allOf', { account: account.display_name })}
              disabled={busy || matching.every((folder) => folder.enabled)}
              onClick={() => {
                requestSwitch(matching, true)
              }}
            >
              {t('mailIndex.all')}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="xs"
              aria-label={t('mailIndex.noneOf', { account: account.display_name })}
              disabled={busy || matching.every((folder) => !folder.enabled)}
              onClick={() => {
                requestSwitch(matching, false)
              }}
            >
              {t('mailIndex.none')}
            </Button>
          </>
        )}
      </div>
      {confirming !== null && (
        <ConfirmRemoval
          question={t('mailIndex.confirmOff', { count: confirming.length, account: account.display_name })}
          confirmLabel={t('mailIndex.confirmOffYes')}
          cancelLabel={t('mailIndex.cancel')}
          pending={switching}
          onConfirm={() => void switchAll(confirming, false)}
          onCancel={() => {
            setConfirming(null)
          }}
        />
      )}
      {progress !== null && progress.total > 1 && (
        <p role="status" className="text-sm text-muted-foreground">
          {t('mailIndex.switching', { done: progress.done, count: progress.total })}
        </p>
      )}
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
          {shown.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('mailIndex.noMatch')}</p>
          ) : (
            <ul aria-label={t('mailIndex.folders', { account: account.display_name })} className="flex flex-col rounded-lg border py-1">
              {shown.map((node) => (
                <IndexTreeItem
                  key={node.path}
                  node={node}
                  idPrefix={headingId}
                  disabled={busy}
                  onSwitch={requestSwitch}
                />
              ))}
            </ul>
          )}
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

interface TreeItemProps {
  node: IndexNode
  /** Makes the checkbox's id unique on the page, with the node's path. */
  idPrefix: string
  disabled: boolean
  onSwitch: (targets: readonly IndexFolder[], enabled: boolean) => void
}

/**
 * A level of the tree: its checkbox ticks it and every switchable folder shown under it, and shows
 * mixed when some of them are on; a folder the user cannot switch (a Gmail label, a live folder)
 * shows its own state, disabled, even with switchable folders under it (their own checkboxes switch
 * them). The checkbox is named by the whole path; the line under it says
 * what the index holds of the folder.
 */
function IndexTreeItem({ node, idPrefix, disabled, onSwitch }: TreeItemProps): ReactElement {
  const { t, i18n } = useTranslation()
  const timeZone = useTimeZone()
  const inputId = `${idPrefix}-${useId()}`
  const { folder } = node
  // A folder the user cannot switch has no tick of its own to take from the folders under it.
  const tick = folder !== null && !isSwitchable(folder) ? null : tickState(node)
  const described = folder === null ? undefined : `${inputId}-state`
  return (
    <li className="flex flex-col">
      <div className="flex items-start gap-2 px-3 py-1.5">
        <input
          id={inputId}
          type="checkbox"
          className="mt-1"
          ref={(input) => {
            if (input !== null) {
              input.indeterminate = tick === 'mixed'
            }
          }}
          checked={tick === null ? (folder?.enabled ?? false) : tick === 'on'}
          disabled={tick === null || disabled}
          aria-label={node.path}
          aria-describedby={described}
          onChange={() => {
            onSwitch(switchableUnder(node), tick !== 'on')
          }}
        />
        <div className="min-w-0">
          <label htmlFor={inputId} className="block truncate text-sm font-medium" title={node.path}>
            {node.name}
          </label>
          {folder !== null && (
            <p id={described} className="text-xs text-muted-foreground">
              {folderState(t, i18n.language, timeZone, folder)}
            </p>
          )}
        </div>
      </div>
      {node.children.length > 0 && (
        <ul className="ml-5 flex flex-col border-l">
          {node.children.map((child) => (
            <IndexTreeItem key={child.path} node={child} idPrefix={idPrefix} disabled={disabled} onSwitch={onSwitch} />
          ))}
        </ul>
      )}
    </li>
  )
}

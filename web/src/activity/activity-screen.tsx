import type { TFunction } from 'i18next'
import { useCallback, useMemo, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { ChevronRight } from 'lucide-react'

import { AccountName } from '@/accounts/account-name.tsx'
import { AccountsContext, useAccountLabel } from '@/accounts/accounts-context.ts'
import { listAccounts } from '@/api/accounts.ts'
import { ACTORS, auditFilesSchema, MAIL_ACTIONS, listAudit, type AuditFilters, type AuditPage, type AuditRecord } from '@/api/audit.ts'
import { apiErrorMessage } from '@/api/http.ts'
import { useTimeZone } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { formatDateTime } from '@/lib/format.ts'
import { useLoad } from '@/lib/use-load.ts'
import { useUnmountSignal } from '@/lib/use-unmount-signal.ts'
import { formatSize } from '@/mail/format.ts'

import { auditFilters, recordApp, RESULTS, validateActivitySearch, type ActivitySearch } from './activity-search.ts'
import { useAppChoices } from './app-names.ts'
import { ACTIVITY_NS } from './i18n.ts'

const selectClass = 'h-9 w-full rounded-md border bg-transparent px-2 text-sm'

type More = { kind: 'idle' } | { kind: 'loading' } | { kind: 'failed'; message: string }

/** Who acted, as a reader names them: you, the connected app, or the rule set. */
function who(t: TFunction, record: AuditRecord): string {
  if (record.actor === 'agent') {
    if (record.app === null) {
      return record.token_id === null ? t('who.agentUnnamed') : t('who.agentToken')
    }
    return t('who.agentNamed', { app: record.app })
  }
  if (record.actor === 'filter') {
    return record.rule_set === null ? t('who.filterGone') : t('who.filterNamed', { ruleSet: record.rule_set })
  }
  return t('who.user')
}

/** A stored value as the reader's language words it: yes or no, a known state, else as kept. */
function text(t: TFunction, name: string, value: unknown): string {
  if (Array.isArray(value)) {
    return value.map(String).join(', ')
  }
  if (typeof value === 'boolean') {
    return value ? t('values.yes') : t('values.no')
  }
  if (typeof value === 'string') {
    return t(`values.${name}.${value}`, { defaultValue: value })
  }
  return typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value)
}

/** The files an agent gave, by name and size. */
function filesText(language: string, value: unknown): string | null {
  const files = auditFilesSchema.safeParse(value)
  return files.success ? files.data.map(({ name, size }) => `${name} (${formatSize(language, size)})`).join(', ') : null
}

/** The line under the summary: who wrote it to whom, or where it went. */
function gist(detail: AuditRecord['detail']): string {
  const word = (name: string): string | null => {
    const value = detail[name]
    return typeof value === 'string' && value !== '' ? value : null
  }
  const to = ['to', 'cc', 'bcc'].flatMap((name) => {
    const value = detail[name]
    return Array.isArray(value) ? value.map(String) : []
  })
  const destination = word('destination')
  const parts = [
    word('from'),
    to.length > 0 ? `→ ${to.join(', ')}` : null,
    word('folder'),
    destination === null ? null : `→ ${destination}`,
  ]
  return parts.filter((part) => part !== null).join(' ')
}

/**
 * The user's audit trail, newest first, a page at a time, filtered by account, who acted, app,
 * action and result, as the URL says. Each row opens to every field kept: never a subject or a body.
 */
export function ActivityScreen(): ReactElement {
  const { t } = useTranslation([ACTIVITY_NS, 'common'])
  // Validated again: the router leaves in what its validation left out.
  const search = useSearch({
    from: '/activity',
    select: (raw) => validateActivitySearch({ ...raw }),
    structuralSharing: true,
  })
  const navigate = useNavigate()
  const filters = useMemo(() => auditFilters(search), [search])
  const apps = useAppChoices(search.app)
  const accounts = useLoad(
    useCallback((signal: AbortSignal) => listAccounts(signal), []),
    () => '',
  )
  // Until the accounts are loaded, a record's account has no name yet, rather than a removed one.
  const loaded = accounts.state.kind === 'loaded' ? accounts.state.value : null

  const accountChoices = (loaded ?? []).map(({ id, display_name }) => ({ id, name: display_name }))
  if (search.account !== undefined && !accountChoices.some(({ id }) => id === search.account)) {
    accountChoices.push({ id: search.account, name: loaded === null ? t('filters.chosen') : t('common:accounts.unknown') })
  }

  function choose(key: keyof ActivitySearch, value: string): void {
    // In place: Back leaves the page rather than undoing each choice.
    void navigate({ to: '/activity', search: validateActivitySearch({ ...search, [key]: value }), replace: true })
  }

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 p-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t('title')}</h1>
      <p className="text-sm text-muted-foreground">{t('intro')}</p>
      <fieldset className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-5">
        <legend className="sr-only">{t('filters.label')}</legend>
        <Choice label={t('filters.account')} value={search.account === undefined ? undefined : String(search.account)} onChange={(v) => { choose('account', v) }}>
          {accountChoices.map(({ id, name }) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </Choice>
        <Choice label={t('filters.who')} value={search.actor} onChange={(v) => { choose('actor', v) }}>
          {ACTORS.map((actor) => (
            <option key={actor} value={actor}>
              {t(`who.${actor}`)}
            </option>
          ))}
        </Choice>
        <Choice label={t('filters.app')} value={search.app} onChange={(v) => { choose('app', v) }}>
          {apps.map(({ key, name }) => (
            <option key={key} value={key}>
              {name}
            </option>
          ))}
        </Choice>
        <Choice label={t('filters.action')} value={search.action} onChange={(v) => { choose('action', v) }}>
          {MAIL_ACTIONS.map((action) => (
            <option key={action} value={action}>
              {t(`actions.${action}`)}
            </option>
          ))}
        </Choice>
        <Choice label={t('filters.result')} value={search.result} onChange={(v) => { choose('result', v) }}>
          {RESULTS.map((result) => (
            <option key={result} value={result}>
              {t(`results.${result}`)}
            </option>
          ))}
        </Choice>
      </fieldset>
      {/* A new list for new filters: nothing of the old one (a page, a Load more on its way) is kept. */}
      <AccountsContext value={loaded}>
        <Records key={JSON.stringify(filters)} filters={filters} />
      </AccountsContext>
    </main>
  )
}

function Choice({
  label,
  value,
  onChange,
  children,
}: {
  label: string
  value: string | undefined
  onChange: (value: string) => void
  children: ReactElement | ReactElement[]
}): ReactElement {
  const { t } = useTranslation(ACTIVITY_NS)
  return (
    <label className="flex min-w-0 flex-col gap-1 text-sm font-medium">
      {label}
      <select
        className={selectClass}
        value={value ?? ''}
        onChange={(event) => {
          onChange(event.target.value)
        }}
      >
        <option value="">{t('filters.any')}</option>
        {children}
      </select>
    </label>
  )
}

function Records({ filters }: { filters: AuditFilters }): ReactElement {
  const { t } = useTranslation(ACTIVITY_NS)
  const load = useCallback((signal: AbortSignal) => listAudit(filters, null, signal), [filters])
  const { state, retry } = useLoad(load, (error) => apiErrorMessage(error, t('loadFailed')))
  const [later, setLater] = useState<AuditPage[]>([])
  const [more, setMore] = useState<More>({ kind: 'idle' })
  const filtered = Object.values(filters).some((value) => value !== undefined)
  const unmountSignal = useUnmountSignal()

  if (state.kind === 'loading') {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        {t('loading')}
      </p>
    )
  }
  if (state.kind === 'failed') {
    return (
      <div className="flex flex-col items-start gap-3">
        <p role="alert" className="text-sm text-destructive">
          {state.message}
        </p>
        <Button variant="outline" size="sm" onClick={retry}>
          {t('retry')}
        </Button>
      </div>
    )
  }
  const pages = [state.value, ...later]
  const records = pages.flatMap((page) => page.records)
  const nextCursor = pages[pages.length - 1]?.next_cursor ?? null

  async function loadMore(cursor: string): Promise<void> {
    setMore({ kind: 'loading' })
    const signal = unmountSignal()
    try {
      const page = await listAudit(filters, cursor, signal)
      setLater((value) => [...value, page])
      setMore({ kind: 'idle' })
    } catch (error) {
      if (!signal.aborted) {
        setMore({ kind: 'failed', message: apiErrorMessage(error, t('loadFailed')) })
      }
    }
  }

  if (records.length === 0) {
    return <p className="text-sm text-muted-foreground">{filtered ? t('noMatches') : t('none')}</p>
  }
  return (
    <div className="flex flex-col gap-3">
      <ol aria-label={t('list')} className="flex flex-col divide-y rounded-lg border">
        {records.map((record) => (
          <li key={record.id}>
            <Row record={record} />
          </li>
        ))}
      </ol>
      {more.kind === 'failed' && (
        <p role="alert" className="text-sm text-destructive">
          {more.message}
        </p>
      )}
      {nextCursor !== null && (
        <Button
          variant="outline"
          size="sm"
          className="self-center"
          aria-disabled={more.kind === 'loading'}
          onClick={() => {
            if (more.kind !== 'loading') {
              void loadMore(nextCursor)
            }
          }}
        >
          {more.kind === 'loading' ? t('loadingMore') : t('loadMore')}
        </Button>
      )}
    </div>
  )
}

function Row({ record }: { record: AuditRecord }): ReactElement {
  const { t, i18n } = useTranslation(ACTIVITY_NS)
  const timeZone = useTimeZone()
  const accountLabel = useAccountLabel()
  const ok = record.result === 'ok'
  const app = record.actor === 'agent' ? recordApp(record) : undefined
  const unlisted = record.detail['not_listed']
  const fields = Object.entries(record.detail).filter(
    ([name, value]) => name !== 'not_listed' && name !== 'filter_id' && value !== null && value !== undefined,
  )
  return (
    <details className="group">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 p-3 text-sm">
        <ChevronRight
          aria-hidden="true"
          className="size-4 text-muted-foreground transition-transform group-open:rotate-90"
        />
        <span className="font-medium tabular-nums">{formatDateTime(i18n.language, record.at, timeZone)}</span>
        <span>{who(t, record)}</span>
        <span>{t(`actions.${record.action}`, { defaultValue: record.action })}</span>
        <AccountName account={accountLabel(record.account_id)} className="text-muted-foreground" />
        <span className={`ml-auto ${ok ? '' : 'text-destructive'}`}>
          {ok ? t('results.ok') : `${t('results.failed')}: ${record.result}`}
        </span>
        <span className="w-full truncate pl-7 text-muted-foreground">
          {typeof unlisted === 'number' ? t('notListed', { count: unlisted }) : gist(record.detail)}
        </span>
      </summary>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 px-10 pb-3 text-sm">
        {record.run_id !== null && (
          <>
            <dt className="text-muted-foreground">{t('fields.run')}</dt>
            <dd>{record.run_id}</dd>
          </>
        )}
        {app !== undefined && (
          <>
            <dt className="text-muted-foreground">{t('filters.app')}</dt>
            <dd className="break-words">
              <Link to="/activity" search={{ app }} className="underline underline-offset-4">
                {t('app.only', { name: who(t, record) })}
              </Link>
            </dd>
          </>
        )}
        {fields.map(([name, value]) => (
          <div key={name} className="contents">
            <dt className="text-muted-foreground">{t(`fields.${name}`, { defaultValue: name })}</dt>
            <dd className="break-all">
              {(name === 'destination_account' || name === 'source_account') && typeof value === 'number'
                ? accountLabel(value).display_name
                : (name === 'files' ? filesText(i18n.language, value) : null) ?? text(t, name, value)}
            </dd>
          </div>
        ))}
      </dl>
    </details>
  )
}

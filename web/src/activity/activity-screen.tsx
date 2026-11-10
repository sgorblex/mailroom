import type { TFunction } from 'i18next'
import { useCallback, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRight } from 'lucide-react'

import { listAccounts } from '@/api/accounts.ts'
import { ACTORS, MAIL_ACTIONS, listAudit, type Actor, type AuditFilters, type AuditPage, type AuditRecord } from '@/api/audit.ts'
import { apiErrorMessage } from '@/api/http.ts'
import { Button } from '@/components/ui/button.tsx'
import { formatDateTime } from '@/lib/format.ts'
import { useLoad } from '@/lib/use-load.ts'
import { useUnmountSignal } from '@/lib/use-unmount-signal.ts'

import { ACTIVITY_NS } from './i18n.ts'

const selectClass = 'h-9 w-full rounded-md border bg-transparent px-2 text-sm'

type More = { kind: 'idle' } | { kind: 'loading' } | { kind: 'failed'; message: string }

/** Who acted, as a reader names them: you, the connected app, or the rule set. */
function who(t: TFunction, record: AuditRecord): string {
  if (record.actor === 'agent') {
    return record.app === null ? t('who.agentUnnamed') : t('who.agentNamed', { app: record.app })
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
 * The user's audit trail, newest first, a page at a time, filtered by account, who acted, action
 * and result. Each row opens to every field kept: never a subject or a body.
 */
export function ActivityScreen(): ReactElement {
  const { t } = useTranslation(ACTIVITY_NS)
  const [filters, setFilters] = useState<AuditFilters>({})
  const accounts = useLoad(
    useCallback((signal: AbortSignal) => listAccounts(signal), []),
    () => '',
  )
  const accountIds = accounts.state.kind === 'loaded' ? accounts.state.value.map((account) => account.id) : []

  function choose(key: keyof AuditFilters, value: string): void {
    setFilters((current) => ({ ...current, [key]: value === '' ? undefined : value }))
  }

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 p-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t('title')}</h1>
      <p className="text-sm text-muted-foreground">{t('intro')}</p>
      <fieldset className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <legend className="sr-only">{t('filters.label')}</legend>
        <Choice label={t('filters.account')} value={filters.account_id} onChange={(v) => { choose('account_id', v) }}>
          {accountIds.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </Choice>
        <Choice label={t('filters.who')} value={filters.actor} onChange={(v) => { choose('actor', v) }}>
          {ACTORS.map((actor: Actor) => (
            <option key={actor} value={actor}>
              {t(`who.${actor}`)}
            </option>
          ))}
        </Choice>
        <Choice label={t('filters.action')} value={filters.action} onChange={(v) => { choose('action', v) }}>
          {MAIL_ACTIONS.map((action) => (
            <option key={action} value={action}>
              {t(`actions.${action}`)}
            </option>
          ))}
        </Choice>
        <Choice label={t('filters.result')} value={filters.result} onChange={(v) => { choose('result', v) }}>
          <option value="ok">{t('results.ok')}</option>
          <option value="failed">{t('results.failed')}</option>
        </Choice>
      </fieldset>
      {/* A new list for new filters: nothing of the old one (a page, a Load more on its way) is kept. */}
      <Records key={JSON.stringify(filters)} filters={filters} />
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
    <label className="flex flex-col gap-1 text-sm font-medium">
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
  const ok = record.result === 'ok'
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
        <span className="font-medium tabular-nums">{formatDateTime(i18n.language, record.at)}</span>
        <span>{who(t, record)}</span>
        <span>{t(`actions.${record.action}`, { defaultValue: record.action })}</span>
        <span className="text-muted-foreground">{record.account_id}</span>
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
        {fields.map(([name, value]) => (
          <div key={name} className="contents">
            <dt className="text-muted-foreground">{t(`fields.${name}`, { defaultValue: name })}</dt>
            <dd className="break-all">{text(t, name, value)}</dd>
          </div>
        ))}
      </dl>
    </details>
  )
}

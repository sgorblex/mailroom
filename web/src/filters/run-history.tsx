import type { TFunction } from 'i18next'
import { useCallback, useEffect, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRight } from 'lucide-react'

import { listRuns, type Filter, type Run } from '@/api/filters.ts'
import { Button } from '@/components/ui/button.tsx'
import { formatDateTime } from '@/lib/format.ts'
import { useLoad } from '@/lib/use-load.ts'

import { filterErrorMessage, runErrorMessage } from './messages.ts'
import { RunReportView } from './run-report.tsx'
import type { Vocabulary } from './vocabulary.ts'
import { FILTERS_NS } from './i18n.ts'

function runStatus(t: TFunction, run: Run): string {
  if (run.started_at === null) {
    return t('filters.history.queued')
  }
  if (run.finished_at === null) {
    return t('filters.history.running')
  }
  if (run.error !== null) {
    return t('filters.history.failed')
  }
  const matched = (run.result?.filters ?? []).reduce(
    (total, entry) => total + (entry.matched_total ?? entry.matched.length),
    0,
  )
  return t('filters.report.matched', { count: matched })
}

/** How often the history reloads while a run is queued or running, in milliseconds. */
const POLL_MS = 2000

/**
 * A rule set's runs, newest first; each opens to its report. `version` changing reloads it, and it
 * follows queued and running runs until they finish.
 */
export function RunHistory({
  ruleSetId,
  filters,
  vocabulary,
  version,
  onRefresh,
}: {
  ruleSetId: number
  filters: Filter[]
  vocabulary: Vocabulary
  version: number
  onRefresh: () => void
}): ReactElement {
  const { t, i18n } = useTranslation(FILTERS_NS)
  const [poll, setPoll] = useState(0)
  const load = useCallback((signal: AbortSignal) => listRuns(ruleSetId, signal), [ruleSetId])
  const { state } = useLoad(
    load,
    (error) => filterErrorMessage(t, error, t('filters.history.loadFailed')),
    `${String(version)}-${String(poll)}`,
  )
  const pending = state.kind === 'loaded' && state.value.some((run) => run.finished_at === null)

  useEffect(() => {
    if (!pending) {
      return
    }
    const timer = setTimeout(() => {
      setPoll((n) => n + 1)
    }, POLL_MS)
    return () => {
      clearTimeout(timer)
    }
  }, [pending, state])

  return (
    <section aria-labelledby="run-history-title" className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <h2 id="run-history-title" className="text-lg font-medium">
          {t('filters.history.title')}
        </h2>
        <Button variant="ghost" size="sm" onClick={onRefresh}>
          {t('filters.history.refresh')}
        </Button>
      </div>
      {state.kind === 'loading' && (
        <p role="status" className="text-sm text-muted-foreground">
          {t('filters.history.loading')}
        </p>
      )}
      {state.kind === 'failed' && (
        <p role="alert" className="text-sm text-destructive">
          {state.message}
        </p>
      )}
      {state.kind === 'loaded' && state.value.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('filters.history.none')}</p>
      )}
      {state.kind === 'loaded' && state.value.length > 0 && (
        <ol aria-label={t('filters.history.title')} className="flex flex-col divide-y rounded-lg border">
          {state.value.map((run) => (
            <li key={run.id}>
              <details className="group">
                <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 p-3 text-sm">
                  <ChevronRight
                    aria-hidden="true"
                    className="size-4 text-muted-foreground transition-transform group-open:rotate-90"
                  />
                  <span className="font-medium tabular-nums">
                    {formatDateTime(i18n.language, run.started_at ?? run.created_at)}
                  </span>
                  <span className="text-muted-foreground">
                    {run.dry_run
                      ? t('filters.history.dryRun')
                      : t(`filters.history.triggers.${run.trigger}`, { defaultValue: run.trigger })}
                  </span>
                  <span className={`ml-auto ${run.error === null ? '' : 'text-destructive'}`}>{runStatus(t, run)}</span>
                </summary>
                <div className="flex flex-col gap-3 px-3 pb-3">
                  {run.result === null ? (
                    run.error !== null && (
                      <p className="text-sm text-destructive">
                        {t('filters.report.error', { message: runErrorMessage(t, run.error) })}
                      </p>
                    )
                  ) : (
                    // The report carries the error of a run stopped part-way.
                    <RunReportView report={run.result} filters={filters} vocabulary={vocabulary} level={3} />
                  )}
                </div>
              </details>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

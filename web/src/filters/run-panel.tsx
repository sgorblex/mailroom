import { useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import type { Account } from '@/api/accounts.ts'
import { dryRun, runNow, type Filter, type RuleSet, type RunReport } from '@/api/filters.ts'
import { Button } from '@/components/ui/button.tsx'

import { filterErrorMessage, runErrorMessage } from './messages.ts'
import { RunReportView } from './run-report.tsx'
import type { Vocabulary } from './vocabulary.ts'
import { FILTERS_NS } from './i18n.ts'

type DryRunState =
  | { kind: 'idle' }
  | { kind: 'running' }
  | { kind: 'failed'; message: string }
  | { kind: 'done'; report: RunReport }

type RunState = { kind: 'idle' } | { kind: 'sending' } | { kind: 'failed'; message: string } | { kind: 'queued' }

/** Dry run and Run now; Run now is blocked while the account's Run filters is off or the rule set disabled. */
export function RunPanel({
  ruleSet,
  account,
  filters,
  vocabulary,
  onRan,
}: {
  ruleSet: RuleSet
  /** `undefined` when the account is gone. */
  account: Account | undefined
  filters: Filter[]
  vocabulary: Vocabulary
  onRan: () => void
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const [dry, setDry] = useState<DryRunState>({ kind: 'idle' })
  const [run, setRun] = useState<RunState>({ kind: 'idle' })

  const runBlocked =
    account === undefined
      ? t('filters.run.blockedNoAccount', { id: ruleSet.account_id })
      : !account.run_filters
        ? t('filters.run.blockedRunFilters', { id: ruleSet.account_id })
        : !ruleSet.enabled
          ? t('filters.run.blockedDisabled')
          : null

  async function startDryRun(): Promise<void> {
    setDry({ kind: 'running' })
    try {
      const done = await dryRun(ruleSet.id)
      if (done.result === null) {
        const message = done.error === null ? t('filters.dryRun.failed') : runErrorMessage(t, done.error)
        setDry({ kind: 'failed', message })
      } else {
        setDry({ kind: 'done', report: done.result })
      }
    } catch (failure) {
      setDry({ kind: 'failed', message: filterErrorMessage(t, failure, t('filters.dryRun.failed')) })
    }
    // Dry runs are recorded too.
    onRan()
  }

  async function startRun(): Promise<void> {
    setRun({ kind: 'sending' })
    try {
      await runNow(ruleSet.id)
      setRun({ kind: 'queued' })
      onRan()
    } catch (failure) {
      setRun({ kind: 'failed', message: filterErrorMessage(t, failure, t('filters.run.failed')) })
    }
  }

  return (
    <section aria-labelledby="run-title" className="flex flex-col gap-4">
      <h2 id="run-title" className="text-lg font-medium">
        {t('filters.run.title')}
      </h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col items-start gap-2 rounded-lg border p-4">
          <Button
            variant="outline"
            disabled={dry.kind === 'running'}
            aria-describedby="dry-run-hint"
            onClick={() => void startDryRun()}
          >
            {t('filters.dryRun.button')}
          </Button>
          <p id="dry-run-hint" className="text-sm text-muted-foreground">
            {t('filters.dryRun.hint')}
          </p>
        </div>
        <div className="flex flex-col items-start gap-2 rounded-lg border p-4">
          <Button
            disabled={runBlocked !== null || run.kind === 'sending'}
            aria-describedby="run-now-hint"
            onClick={() => void startRun()}
          >
            {t('filters.run.button')}
          </Button>
          <p id="run-now-hint" className="text-sm text-muted-foreground">
            {runBlocked ?? t('filters.run.hint')}
          </p>
        </div>
      </div>
      {run.kind === 'queued' && (
        <p role="status" className="text-sm">
          {t('filters.run.queued')}
        </p>
      )}
      {run.kind === 'failed' && (
        <p role="alert" className="text-sm text-destructive">
          {run.message}
        </p>
      )}
      {dry.kind === 'running' && (
        <p role="status" className="text-sm text-muted-foreground">
          {t('filters.dryRun.running')}
        </p>
      )}
      {dry.kind === 'failed' && (
        <p role="alert" className="text-sm text-destructive">
          {dry.message}
        </p>
      )}
      {dry.kind === 'done' && (
        <section aria-labelledby="dry-run-title" className="flex flex-col gap-3 rounded-lg border bg-muted/30 p-4">
          <h3 id="dry-run-title" className="font-medium">
            {t('filters.dryRun.result')}
          </h3>
          <p className="text-sm text-muted-foreground">{t('filters.dryRun.caveat')}</p>
          <RunReportView report={dry.report} filters={filters} vocabulary={vocabulary} level={4} />
        </section>
      )}
    </section>
  )
}

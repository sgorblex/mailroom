import { useId, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import type { Account, AccountId } from '@/api/accounts.ts'
import { ALL_ACCOUNTS, dryRun, runNow, type Filter, type RuleSet, type RunReport } from '@/api/filters.ts'
import { Button } from '@/components/ui/button.tsx'

import { selectClass } from './folder-picker.tsx'
import { filterErrorMessage, runErrorMessage } from './messages.ts'
import { RunReportView } from './run-report.tsx'
import type { Vocabulary } from './vocabulary.ts'
import { FILTERS_NS } from './i18n.ts'

type DryRunState =
  | { kind: 'idle' }
  | { kind: 'running' }
  | { kind: 'failed'; message: string }
  | { kind: 'done'; report: RunReport; account: AccountId }

type RunState =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'failed'; message: string }
  | { kind: 'queued'; count: number }

/**
 * Dry run and Run now; Run now is blocked while the account's Run filters is off or the rule set
 * disabled. A rule set for all accounts is dry run on the account the user picks, and runs on each
 * account whose Run filters is on.
 */
export function RunPanel({
  ruleSet,
  account,
  accounts,
  filters,
  vocabulary,
  onRan,
}: {
  ruleSet: RuleSet
  /** `undefined` for all accounts, or when the account is gone. */
  account: Account | undefined
  /** The user's accounts. */
  accounts: Account[]
  filters: Filter[]
  vocabulary: Vocabulary
  onRan: () => void
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const tryOnId = useId()
  const allAccounts = ruleSet.account_id === ALL_ACCOUNTS
  const [dry, setDry] = useState<DryRunState>({ kind: 'idle' })
  const [run, setRun] = useState<RunState>({ kind: 'idle' })
  const [tryOn, setTryOn] = useState<AccountId | undefined>(accounts[0]?.id)
  const running = accounts.filter((candidate) => candidate.run_filters === true)

  const runBlocked = allAccounts
    ? running.length === 0
      ? t('filters.allAccounts.runBlocked')
      : !ruleSet.enabled
        ? t('filters.run.blockedDisabled')
        : null
    : account === undefined
      ? t('filters.run.blockedNoAccount')
      : account.run_filters !== true
        ? t('filters.run.blockedRunFilters', { account: account.display_name })
        : !ruleSet.enabled
          ? t('filters.run.blockedDisabled')
          : null
  const accountName = (id: AccountId): string =>
    accounts.find((candidate) => candidate.id === id)?.display_name ?? t('accounts.unknown', { ns: 'common' })

  async function startDryRun(): Promise<void> {
    if (allAccounts && tryOn === undefined) {
      return
    }
    setDry({ kind: 'running' })
    try {
      const done = await dryRun(ruleSet.id, allAccounts ? tryOn : undefined)
      if (done.result === null) {
        const message = done.error === null ? t('filters.dryRun.failed') : runErrorMessage(t, done.error)
        setDry({ kind: 'failed', message })
      } else {
        setDry({ kind: 'done', report: done.result, account: done.account_id })
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
      const queued = await runNow(ruleSet.id)
      setRun({ kind: 'queued', count: queued.length })
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
          {allAccounts && (
            <div className="flex w-full flex-col gap-1">
              <label htmlFor={tryOnId} className="text-sm font-medium">
                {t('filters.allAccounts.tryOn')}
              </label>
              <select
                id={tryOnId}
                className={selectClass}
                value={tryOn === undefined ? '' : String(tryOn)}
                onChange={(event) => {
                  setTryOn(Number(event.target.value))
                }}
              >
                {accounts.map((candidate) => (
                  <option key={candidate.id} value={String(candidate.id)}>
                    {candidate.display_name}
                  </option>
                ))}
              </select>
            </div>
          )}
          <Button
            variant="outline"
            disabled={dry.kind === 'running' || (allAccounts && tryOn === undefined)}
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
            {runBlocked ??
              (allAccounts ? t('filters.allAccounts.runHint', { count: running.length }) : t('filters.run.hint'))}
          </p>
        </div>
      </div>
      {run.kind === 'queued' && (
        <p role="status" className="text-sm">
          {allAccounts ? t('filters.allAccounts.queued', { count: run.count }) : t('filters.run.queued')}
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
            {allAccounts
              ? t('filters.allAccounts.dryRunOn', { account: accountName(dry.account) })
              : t('filters.dryRun.result')}
          </h3>
          <p className="text-sm text-muted-foreground">{t('filters.dryRun.caveat')}</p>
          <RunReportView report={dry.report} filters={filters} vocabulary={vocabulary} level={4} />
        </section>
      )}
    </section>
  )
}

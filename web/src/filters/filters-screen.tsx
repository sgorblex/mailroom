import { useCallback, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from '@tanstack/react-router'

import { listAccounts, type Account } from '@/api/accounts.ts'
import {
  addRuleSet,
  changeRuleSet,
  listRuleSets,
  removeRuleSet,
  type RuleSet,
  type RuleSetFields,
} from '@/api/filters.ts'
import { apiErrorMessage } from '@/api/http.ts'
import { ConfirmRemoval } from '@/components/confirm-removal.tsx'
import { Button } from '@/components/ui/button.tsx'
import { Switch } from '@/components/ui/switch.tsx'
import { useLoad } from '@/lib/use-load.ts'

import { folderLabel, triggerSummary } from './describe.ts'
import { useFolders } from './folders.ts'
import { filterErrorMessage } from './messages.ts'
import { RuleSetForm } from './rule-set-form.tsx'
import { FILTERS_NS } from './i18n.ts'

/** The signed-in user's rule sets, account by account; each opens in its own editor. */
export function FiltersScreen(): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const { state: accounts, retry } = useLoad(listAccounts, (error) =>
    apiErrorMessage(error, t('accounts.loadFailed')),
  )

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 p-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t('filters.title')}</h1>
        <p className="max-w-[65ch] text-sm text-muted-foreground">{t('filters.intro')}</p>
      </header>
      {accounts.kind === 'loading' && (
        <p role="status" className="text-muted-foreground">
          {t('accounts.loading')}
        </p>
      )}
      {accounts.kind === 'failed' && (
        <div className="flex flex-col items-start gap-3">
          <p role="alert">{accounts.message}</p>
          <Button variant="outline" size="sm" onClick={retry}>
            {t('auth.retry')}
          </Button>
        </div>
      )}
      {accounts.kind === 'loaded' && accounts.value.length === 0 && (
        <p className="text-muted-foreground">
          {t('filters.noAccounts')}{' '}
          <Link to="/accounts" className="text-foreground underline underline-offset-4">
            {t('filters.goToAccounts')}
          </Link>
        </p>
      )}
      {accounts.kind === 'loaded' &&
        accounts.value.map((account) => <AccountRuleSets key={account.id} account={account} />)}
    </main>
  )
}

function AccountRuleSets({ account }: { account: Account }): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const navigate = useNavigate()
  const load = useCallback((signal: AbortSignal) => listRuleSets(account.id, signal), [account.id])
  const { state: ruleSets, retry: reload } = useLoad(load, (error) =>
    filterErrorMessage(t, error, t('filters.loadFailed')),
  )
  const [adding, setAdding] = useState(false)
  const addTrigger = useRef<HTMLButtonElement>(null)

  const headingId = `account-${account.id}-rule-sets`
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id={headingId} className="text-lg font-medium">
          {account.id}
        </h2>
        <p className="text-sm text-muted-foreground">
          {account.run_filters ? t('filters.runFiltersOn') : t('filters.runFiltersOff')}{' '}
          {!account.run_filters && (
            <Link to="/accounts" className="text-foreground underline underline-offset-4">
              {t('filters.goToAccounts')}
            </Link>
          )}
        </p>
      </div>
      {ruleSets.kind === 'loading' && (
        <p role="status" className="text-sm text-muted-foreground">
          {t('filters.loading')}
        </p>
      )}
      {ruleSets.kind === 'failed' && (
        <div className="flex flex-col items-start gap-3">
          <p role="alert" className="text-sm">
            {ruleSets.message}
          </p>
          <Button variant="outline" size="sm" onClick={reload}>
            {t('auth.retry')}
          </Button>
        </div>
      )}
      {ruleSets.kind === 'loaded' && ruleSets.value.length === 0 && !adding && (
        <p className="text-sm text-muted-foreground">{t('filters.noRuleSets')}</p>
      )}
      {ruleSets.kind === 'loaded' && ruleSets.value.length > 0 && (
        <ul
          aria-label={t('filters.ruleSetsOf', { id: account.id })}
          className="flex flex-col divide-y rounded-lg border"
        >
          {ruleSets.value.map((ruleSet) => (
            <RuleSetRow
              key={ruleSet.id}
              ruleSet={ruleSet}
              onRemoved={() => {
                reload()
                // The row goes: focus stays in this account's rule sets.
                requestAnimationFrame(() => addTrigger.current?.focus())
              }}
            />
          ))}
        </ul>
      )}
      {adding ? (
        <NewRuleSetForm
          accountId={account.id}
          save={async (fields) => {
            const created = await addRuleSet(account.id, fields)
            await navigate({ to: '/filters/$ruleSetId', params: { ruleSetId: String(created.id) } })
          }}
          onCancel={() => {
            setAdding(false)
            requestAnimationFrame(() => addTrigger.current?.focus())
          }}
        />
      ) : (
        <Button
          ref={addTrigger}
          variant="outline"
          className="self-start"
          aria-label={t('filters.ruleSet.addTo', { id: account.id })}
          onClick={() => {
            setAdding(true)
          }}
        >
          {t('filters.ruleSet.add')}
        </Button>
      )}
    </section>
  )
}

/** The rule set form for a new rule set, with the account's folders to pick from. */
function NewRuleSetForm({
  accountId,
  save,
  onCancel,
}: {
  accountId: string
  save: (fields: RuleSetFields) => Promise<unknown>
  onCancel: () => void
}): ReactElement {
  return <RuleSetForm folders={useFolders(accountId)} save={save} onCancel={onCancel} />
}

function RuleSetRow({ ruleSet, onRemoved }: { ruleSet: RuleSet; onRemoved: () => void }): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const [enabled, setEnabled] = useState(ruleSet.enabled)
  const [confirming, setConfirming] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const removeTrigger = useRef<HTMLButtonElement>(null)

  async function toggle(next: boolean): Promise<void> {
    if (pending) {
      return
    }
    setPending(true)
    setError(null)
    try {
      setEnabled((await changeRuleSet(ruleSet.id, { enabled: next })).enabled)
    } catch (failure) {
      setError(filterErrorMessage(t, failure, t('filters.actionFailed')))
    } finally {
      setPending(false)
    }
  }

  async function remove(): Promise<void> {
    setPending(true)
    setError(null)
    try {
      await removeRuleSet(ruleSet.id)
      onRemoved()
    } catch (failure) {
      setError(filterErrorMessage(t, failure, t('filters.actionFailed')))
      setPending(false)
    }
  }

  return (
    <li className="flex flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-col">
          <Link
            to="/filters/$ruleSetId"
            params={{ ruleSetId: String(ruleSet.id) }}
            className="font-medium underline-offset-4 hover:underline"
          >
            {ruleSet.name}
          </Link>
          <span className="text-sm text-muted-foreground">
            {[
              folderLabel(ruleSet.folder),
              triggerSummary(t, ruleSet),
              ...(ruleSet.starred_only ? [t('filters.ruleSet.starredOnlyShort')] : []),
            ].join(' · ')}
          </span>
        </div>
        <span className="ml-auto flex items-center gap-3">
          <Switch
            checked={enabled}
            // Not `disabled`: that would take focus from the switch just pressed.
            aria-disabled={pending}
            className="aria-disabled:opacity-50"
            aria-label={t('filters.ruleSet.enable', { name: ruleSet.name })}
            onCheckedChange={(next) => void toggle(next)}
          />
          {!confirming && (
            <Button
              ref={removeTrigger}
              variant="outline"
              size="sm"
              aria-label={t('filters.ruleSet.remove', { name: ruleSet.name })}
              onClick={() => {
                setConfirming(true)
                setError(null)
              }}
            >
              {t('filters.remove')}
            </Button>
          )}
        </span>
      </div>
      {confirming && (
        <ConfirmRemoval
          question={t('filters.ruleSet.confirmRemoval', { name: ruleSet.name })}
          confirmLabel={t('filters.confirmRemove')}
          cancelLabel={t('filters.cancel')}
          pending={pending}
          onConfirm={() => void remove()}
          onCancel={() => {
            setConfirming(false)
            requestAnimationFrame(() => removeTrigger.current?.focus())
          }}
        />
      )}
      {error !== null && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </li>
  )
}

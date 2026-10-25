import { useCallback, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'

import { listAccounts, type Account } from '@/api/accounts.ts'
import {
  changeRuleSet,
  getFilterSchema,
  getRuleSet,
  removeRuleSet,
  type Filter,
  type RuleSet,
  type RuleSetWithFilters,
} from '@/api/filters.ts'
import { ApiRequestError } from '@/api/http.ts'
import { Button } from '@/components/ui/button.tsx'

import { ConfirmRemoval } from './confirm-removal.tsx'
import { folderLabel, triggerSummary } from './describe.ts'
import { FilterList } from './filter-list.tsx'
import { useFolders, type FoldersState } from './folders.ts'
import { filterErrorMessage } from './messages.ts'
import { RuleSetForm } from './rule-set-form.tsx'
import { RunHistory } from './run-history.tsx'
import { RunPanel } from './run-panel.tsx'
import { useLoad } from './use-load.ts'
import { readVocabulary, VocabularyError, type Vocabulary } from './vocabulary.ts'
import { FILTERS_NS } from './i18n.ts'

interface Loaded {
  ruleSet: RuleSetWithFilters
  account: Account | undefined
  vocabulary: Vocabulary
}

/** The rule set screen of `/filters/$ruleSetId`; an id that is no id is a rule set not found. */
export function RuleSetRoute(): ReactElement {
  const { ruleSetId } = useParams({ from: '/filters/$ruleSetId' })
  const id = /^[1-9]\d*$/.test(ruleSetId) ? Number(ruleSetId) : null
  return <RuleSetScreen key={ruleSetId} ruleSetId={id !== null && Number.isSafeInteger(id) ? id : null} />
}

/** One rule set: its settings, its ordered filters, dry run, Run now and run history. */
export function RuleSetScreen({ ruleSetId }: { ruleSetId: number | null }): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const load = useCallback(
    async (signal: AbortSignal): Promise<Loaded | null> => {
      if (ruleSetId === null) {
        return null
      }
      const [ruleSet, accounts, schema] = await Promise.all([
        getRuleSet(ruleSetId, signal).catch((error: unknown) => {
          if (error instanceof ApiRequestError && error.status === 404) {
            return null
          }
          throw error
        }),
        listAccounts(signal),
        getFilterSchema(signal),
      ])
      if (ruleSet === null) {
        return null
      }
      const account = accounts.find((candidate) => candidate.id === ruleSet.account_id)
      return { ruleSet, account, vocabulary: readVocabulary(schema) }
    },
    [ruleSetId],
  )
  const { state, retry } = useLoad(load, (error) =>
    error instanceof VocabularyError
      ? t('filters.schemaFailed')
      : filterErrorMessage(t, error, t('filters.ruleSet.loadFailed')),
  )

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 p-6">
      <Link
        to="/filters"
        className="inline-flex items-center gap-1 self-start text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft aria-hidden="true" className="size-4" />
        {t('filters.backToList')}
      </Link>
      {state.kind === 'loading' && (
        <p role="status" className="text-muted-foreground">
          {t('filters.ruleSet.loading')}
        </p>
      )}
      {state.kind === 'failed' && (
        <div className="flex flex-col items-start gap-3">
          <p role="alert">{state.message}</p>
          <Button variant="outline" size="sm" onClick={retry}>
            {t('auth.retry')}
          </Button>
        </div>
      )}
      {state.kind === 'loaded' && state.value === null && <p role="alert">{t('filters.ruleSet.notFound')}</p>}
      {state.kind === 'loaded' && state.value !== null && (
        <RuleSetEditor key={state.value.ruleSet.id} {...state.value} />
      )}
    </main>
  )
}

function RuleSetEditor({ ruleSet: initial, account, vocabulary }: Loaded): ReactElement {
  const { filters: initialFilters, ...initialSettings } = initial
  const [ruleSet, setRuleSet] = useState<RuleSet>(initialSettings)
  const [filters, setFilters] = useState<Filter[]>(initialFilters)
  const [historyVersion, setHistoryVersion] = useState(0)
  const folders = useFolders(ruleSet.account_id)
  const refreshHistory = useCallback(() => {
    setHistoryVersion((value) => value + 1)
  }, [])

  return (
    <>
      <RuleSetHeader ruleSet={ruleSet} folders={folders} onChanged={setRuleSet} />
      <FilterList
        ruleSet={ruleSet}
        filters={filters}
        vocabulary={vocabulary}
        folders={folders}
        setFilters={setFilters}
      />
      <RunPanel ruleSet={ruleSet} account={account} filters={filters} vocabulary={vocabulary} onRan={refreshHistory} />
      <RunHistory
        ruleSetId={ruleSet.id}
        filters={filters}
        vocabulary={vocabulary}
        version={historyVersion}
        onRefresh={refreshHistory}
      />
    </>
  )
}

function RuleSetHeader({
  ruleSet,
  folders,
  onChanged,
}: {
  ruleSet: RuleSet
  folders: FoldersState
  onChanged: (ruleSet: RuleSet) => void
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const navigate = useNavigate()
  const [mode, setMode] = useState<'idle' | 'editing' | 'confirming-removal'>('idle')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const editTrigger = useRef<HTMLButtonElement>(null)

  function backToIdle(): void {
    setMode('idle')
    requestAnimationFrame(() => editTrigger.current?.focus())
  }

  async function remove(): Promise<void> {
    setPending(true)
    setError(null)
    try {
      await removeRuleSet(ruleSet.id)
      await navigate({ to: '/filters' })
    } catch (failure) {
      setError(filterErrorMessage(t, failure, t('filters.actionFailed')))
      setPending(false)
    }
  }

  if (mode === 'editing') {
    return (
      <RuleSetForm
        folders={folders}
        ruleSet={ruleSet}
        save={async (fields) => {
          onChanged(await changeRuleSet(ruleSet.id, fields))
          backToIdle()
        }}
        onCancel={backToIdle}
      />
    )
  }
  return (
    <header className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="text-2xl font-semibold tracking-tight">{ruleSet.name}</h1>
          <p className="text-sm text-muted-foreground">
            {[
              ruleSet.account_id,
              folderLabel(ruleSet.folder),
              triggerSummary(t, ruleSet),
              ...(ruleSet.starred_only ? [t('filters.ruleSet.starredOnlyShort')] : []),
              ruleSet.enabled ? t('filters.ruleSet.isEnabled') : t('filters.ruleSet.isDisabled'),
            ].join(' · ')}
          </p>
        </div>
        {mode === 'idle' && (
          <span className="ml-auto flex gap-2">
            <Button
              ref={editTrigger}
              variant="outline"
              size="sm"
              onClick={() => {
                setError(null)
                setMode('editing')
              }}
            >
              {t('filters.ruleSet.editSettings')}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setError(null)
                setMode('confirming-removal')
              }}
            >
              {t('filters.ruleSet.removeThis')}
            </Button>
          </span>
        )}
      </div>
      {mode === 'confirming-removal' && (
        <ConfirmRemoval
          question={t('filters.ruleSet.confirmRemoval', { name: ruleSet.name })}
          pending={pending}
          onConfirm={() => void remove()}
          onCancel={backToIdle}
        />
      )}
      {error !== null && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </header>
  )
}

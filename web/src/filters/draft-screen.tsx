import { useCallback, useId, useMemo, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'

import { AccountName } from '@/accounts/account-name.tsx'
import { useAccountKeywords } from '@/accounts/accounts-context.ts'
import { listAccounts, type Account, type AccountId } from '@/api/accounts.ts'
import {
  addFilter,
  addRuleSet,
  draftFromMessage,
  draftFromSearch,
  getFilterSchema,
  listRuleSets,
  type FilterFields,
  type RuleSet,
} from '@/api/filters.ts'
import { ApiRequestError } from '@/api/http.ts'
import type { SearchQuery } from '@/api/mail.ts'
import type { MailSearch } from '@/mail/route-search.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'
import { useLoad } from '@/lib/use-load.ts'

import { describeCriterion, type Wording } from './describe.ts'
import type { FilterDraftSearch } from './draft-search.ts'
import { FilterEditor, type FilterStart } from './filter-editor.tsx'
import { useFolders } from './folders.ts'
import { filterErrorMessage } from './messages.ts'
import { readSpec } from './spec.ts'
import { readVocabulary, VocabularyError, type Vocabulary } from './vocabulary.ts'
import { FILTERS_NS } from './i18n.ts'

const DRAFT_ACTION = { type: 'no_op' }
const backClass = 'inline-flex items-center gap-1 self-start text-sm text-muted-foreground hover:text-foreground'
const INBOX = ['INBOX']

type Source = { kind: 'message'; ref: string; account: AccountId } | { kind: 'search'; query: SearchQuery }

type Draft =
  | { kind: 'message'; account: AccountId; criteria: Record<string, unknown>[]; name: string }
  | { kind: 'search'; account: AccountId | null; spec: Record<string, unknown>; dropped: string[]; name: string }

interface Loaded {
  draft: Draft
  accounts: Account[]
  vocabulary: Vocabulary
}

function sourceOf(search: FilterDraftSearch): Source | null {
  if (search.message !== undefined && search.account !== undefined) {
    return { kind: 'message', ref: search.message, account: search.account }
  }
  return search.q === undefined ? null : { kind: 'search', query: search.q }
}

/** The search's own account, when it names one rather than all. */
function searchAccount(query: SearchQuery): AccountId | null {
  return query.account !== undefined && /^[1-9]\d*$/.test(query.account) ? Number(query.account) : null
}

async function loadDraft(source: Source, signal: AbortSignal): Promise<Draft> {
  if (source.kind === 'message') {
    const draft = await draftFromMessage(source.ref, signal)
    return { kind: 'message', account: source.account, criteria: draft.criteria, name: draft.name }
  }
  const draft = await draftFromSearch(source.query, signal)
  const { query } = source
  return {
    kind: 'search',
    account: searchAccount(query),
    spec: draft.spec,
    dropped: draft.dropped,
    name: query.from ?? query.subject ?? query.to ?? '',
  }
}

function droppedList(t: TFunction, dropped: unknown): string {
  const fields = Array.isArray(dropped) ? dropped.filter((field) => typeof field === 'string') : []
  return fields.map((field) => t(`filters.draft.fields.${field}`, { defaultValue: field })).join(', ')
}

function failureMessage(t: TFunction, source: Source, error: unknown): string {
  if (error instanceof VocabularyError) {
    return t('filters.schemaFailed')
  }
  if (error instanceof ApiRequestError && error.code === 'nothing_to_filter') {
    if (source.kind === 'message') {
      return t('filters.draft.nothingInMessage')
    }
    const dropped = droppedList(t, error.field('dropped'))
    return dropped === '' ? t('filters.draft.nothingInSearch') : t('filters.draft.nothingInSearchDropped', { fields: dropped })
  }
  return filterErrorMessage(t, error, t('filters.draft.loadFailed'))
}

/** `/filters/new`: a filter drafted from a message or a search, added to a rule set of its inbox. */
export function FilterDraftRoute(): ReactElement {
  const search = useSearch({ from: '/filters/new' })
  const key = JSON.stringify(search)
  // One source per search, so that a render of the route alone loads nothing again.
  const source = useMemo(() => sourceOf(JSON.parse(key) as FilterDraftSearch), [key])
  return <FilterDraftScreen key={key} source={source} />
}

/** The mail screen where the draft came from, to go back to. */
function mailSearch(source: Source): MailSearch {
  return source.kind === 'message' ? { account: source.account, message: source.ref } : { q: source.query }
}

export function FilterDraftScreen({ source }: { source: Source | null }): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const load = useCallback(
    async (signal: AbortSignal): Promise<Loaded | null> => {
      if (source === null) {
        return null
      }
      const [draft, accounts, schema] = await Promise.all([
        loadDraft(source, signal),
        listAccounts(signal),
        getFilterSchema(signal),
      ])
      return { draft, accounts, vocabulary: readVocabulary(schema) }
    },
    [source],
  )
  const { state, retry } = useLoad(load, (error) =>
    source === null ? t('filters.draft.loadFailed') : failureMessage(t, source, error),
  )

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 p-6">
      {source === null ? (
        <Link to="/filters" className={backClass}>
          <ArrowLeft aria-hidden="true" className="size-4" />
          {t('filters.backToList')}
        </Link>
      ) : (
        <Link to="/mail" search={mailSearch(source)} className={backClass}>
          <ArrowLeft aria-hidden="true" className="size-4" />
          {t('filters.draft.back')}
        </Link>
      )}
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          {source?.kind === 'search' ? t('filters.draft.fromSearch') : t('filters.draft.fromMessage')}
        </h1>
        <p className="max-w-[65ch] text-sm text-muted-foreground">{t('filters.draft.intro')}</p>
      </header>
      {state.kind === 'loading' && (
        <p role="status" className="text-muted-foreground">
          {t('filters.draft.loading')}
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
      {state.kind === 'loaded' && state.value === null && <p role="alert">{t('filters.draft.nothingToDraft')}</p>}
      {state.kind === 'loaded' && state.value !== null && source !== null && (
        <DraftEditor {...state.value} source={source} />
      )}
    </main>
  )
}

function DraftEditor({ draft, accounts, vocabulary, source }: Loaded & { source: Source }): ReactElement {
  const { t, i18n } = useTranslation(FILTERS_NS)
  const prefix = useId()
  const fixed = draft.account
  const [accountId, setAccountId] = useState<AccountId | null>(fixed ?? accounts[0]?.id ?? null)
  const [criterionIndex, setCriterionIndex] = useState(0)
  const account = accounts.find((candidate) => candidate.id === accountId)
  if (account === undefined) {
    return <p role="alert">{t('filters.draft.noAccount')}</p>
  }
  const wording: Wording = { t, language: i18n.language, vocabulary }
  const describe = (criterion: Record<string, unknown>): string => {
    try {
      return describeCriterion(wording, readSpec({ criterion, action: DRAFT_ACTION }).criterion)
    } catch {
      return JSON.stringify(criterion)
    }
  }
  const start: FilterStart =
    draft.kind === 'message'
      ? { name: draft.name, spec: { criterion: draft.criteria[0] ?? {}, action: DRAFT_ACTION } }
      : { name: draft.name, spec: draft.spec }

  return (
    <div className="flex flex-col gap-4">
      {fixed === null ? (
        <div className="flex flex-col gap-1">
          <label htmlFor={`${prefix}-account`} className="text-sm font-medium">
            {t('filters.draft.account')}
          </label>
          <select
            id={`${prefix}-account`}
            className="h-9 max-w-80 rounded-md border bg-transparent px-2 text-sm"
            value={account.id}
            onChange={(event) => {
              setAccountId(Number(event.target.value))
            }}
          >
            {accounts.map((each) => (
              <option key={each.id} value={each.id}>
                {each.display_name}
              </option>
            ))}
          </select>
        </div>
      ) : (
        <p className="flex min-w-0 items-center gap-2 text-sm">
          <span className="text-muted-foreground">{t('filters.draft.account')}</span>
          <AccountName account={account} />
        </p>
      )}
      {draft.kind === 'search' && draft.dropped.length > 0 && (
        <p className="text-sm text-muted-foreground">
          {t('filters.draft.dropped', { fields: droppedList(t, draft.dropped) })}
        </p>
      )}
      {draft.kind === 'message' && draft.criteria.length > 1 && (
        <fieldset className="flex flex-col gap-1">
          <legend className="mb-1 text-sm font-medium">{t('filters.draft.matchOn')}</legend>
          {draft.criteria.map((criterion, index) => (
            <label key={index} className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name={`${prefix}-criterion`}
                checked={index === criterionIndex}
                onChange={() => {
                  setCriterionIndex(index)
                }}
              />
              <span className="break-all">{describe(criterion)}</span>
            </label>
          ))}
        </fieldset>
      )}
      <DraftTarget
        account={account}
        targets={accounts.filter((each) => each.id !== account.id)}
        start={start}
        criterion={draft.kind === 'message' ? draft.criteria[criterionIndex] : undefined}
        vocabulary={vocabulary}
        source={source}
      />
    </div>
  )
}

function isInbox(folder: readonly string[]): boolean {
  return folder.length === 1 && folder[0]?.toUpperCase() === 'INBOX'
}

/**
 * The rule set of the account's inbox the filter goes to, a new one or one it has, and the editor,
 * which keeps what the user wrote when the account or the criterion changes.
 */
function DraftTarget({
  account,
  targets,
  start,
  criterion,
  vocabulary,
  source,
}: {
  account: Account
  targets: readonly Account[]
  start: FilterStart
  criterion: Record<string, unknown> | undefined
  vocabulary: Vocabulary
  source: Source
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const navigate = useNavigate()
  const folders = useFolders(account.id)
  const keywordsOf = useAccountKeywords()
  const load = useCallback(
    async (signal: AbortSignal) => ({ account: account.id, ruleSets: await listRuleSets(account.id, signal) }),
    [account.id],
  )
  const { state: loaded, retry } = useLoad(load, (error) => filterErrorMessage(t, error, t('filters.loadFailed')))
  /** A rule set made here before adding the filter failed: the next try adds to it. */
  const [created, setCreated] = useState<RuleSet[]>([])
  const [choice, setChoice] = useState<number | 'new' | null>(null)
  const [shownAccount, setShownAccount] = useState(account.id)
  if (shownAccount !== account.id) {
    setShownAccount(account.id)
    setChoice(null)
  }
  const [newName, setNewName] = useState(() => t('filters.draft.newRuleSetDefault'))
  const prefix = useId()

  // A reload keeps the other account's answer until this one's comes.
  const ready = loaded.kind === 'loaded' && loaded.value.account === account.id
  const inbox = ready
    ? [...loaded.value.ruleSets, ...created].filter(
        (ruleSet) => ruleSet.account_id === account.id && isInbox(ruleSet.folder),
      )
    : []
  const chosen = choice ?? inbox[0]?.id ?? 'new'
  const target = inbox.find((ruleSet) => ruleSet.id === chosen)

  async function save(fields: FilterFields): Promise<void> {
    if (!ready) {
      throw new Error('rule sets not loaded')
    }
    let ruleSet = target
    if (ruleSet === undefined) {
      ruleSet = await addRuleSet(account.id, {
        name: newName.trim(),
        folder: INBOX,
        trigger: 'new_mail',
        interval_minutes: null,
        marker: 'none',
        enabled: false,
      })
      const made = ruleSet
      setCreated((current) => [...current, made])
      setChoice(made.id)
    }
    await addFilter(ruleSet.id, fields)
    await navigate({ to: '/filters/$ruleSetId', params: { ruleSetId: String(ruleSet.id) } })
  }

  return (
    <FilterEditor
      start={start}
      criterion={criterion}
      vocabulary={vocabulary}
      folders={folders}
      keywords={keywordsOf(account.id)}
      targets={targets}
      save={save}
      onCancel={() => {
        void navigate({ to: '/mail', search: mailSearch(source) })
      }}
    >
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-medium">{t('filters.draft.ruleSet')}</legend>
        {loaded.kind === 'failed' ? (
          <div className="flex flex-col items-start gap-3">
            <p role="alert" className="text-sm">
              {loaded.message}
            </p>
            <Button variant="outline" size="sm" onClick={retry}>
              {t('auth.retry')}
            </Button>
          </div>
        ) : !ready ? (
          <p role="status" className="text-sm text-muted-foreground">
            {t('filters.loading')}
          </p>
        ) : (
          <>
        {inbox.map((ruleSet) => (
          <label key={ruleSet.id} className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name={`${prefix}-rule-set`}
              checked={chosen === ruleSet.id}
              onChange={() => {
                setChoice(ruleSet.id)
              }}
            />
            <span className="break-words">{ruleSet.name}</span>
            <span className="text-muted-foreground">
              {ruleSet.enabled ? t('filters.ruleSet.isEnabled') : t('filters.ruleSet.isDisabled')}
            </span>
          </label>
        ))}
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            name={`${prefix}-rule-set`}
            checked={chosen === 'new'}
            onChange={() => {
              setChoice('new')
            }}
          />
          {t('filters.draft.newRuleSet')}
        </label>
        {chosen === 'new' && (
          <div className="ml-6 flex max-w-80 flex-col gap-1">
            <label htmlFor={`${prefix}-new-name`} className="text-sm font-medium">
              {t('filters.draft.newRuleSetName')}
            </label>
            <Input
              id={`${prefix}-new-name`}
              required
              maxLength={200}
              value={newName}
              onChange={(event) => {
                setNewName(event.target.value)
              }}
            />
          </div>
        )}
        <p className="text-sm text-muted-foreground">
          {target === undefined
            ? t('filters.draft.startsOff')
            : target.enabled
              ? t('filters.draft.actsAtOnce')
              : t('filters.draft.ruleSetOff')}
          {account.run_filters !== true && <> {t('filters.runFiltersOff')}</>}
        </p>
          </>
        )}
      </fieldset>
    </FilterEditor>
  )
}

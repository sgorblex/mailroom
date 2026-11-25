import { useId, useRef, useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from '@tanstack/react-router'
import { ListFilter, Search, X } from 'lucide-react'

import type { AccountId, Account } from '@/api/accounts.ts'
import { SEARCH_FLAG_FIELDS, type SearchQuery } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { buttonVariants } from '@/components/ui/button-variants.ts'
import { Input } from '@/components/ui/input.tsx'
import { cn } from '@/lib/utils.ts'

import { useFiltersOffered } from './filter-draft.ts'
import { SaveViewButton } from './saved-views.tsx'
import { ALL_MAIL, searchedIn, type SearchScope } from './search-scope.ts'
import { listQuery, type QuickFilter } from './route-search.ts'
import { MAIL_NS } from './i18n.ts'
import {
  accountTerm,
  flagValues,
  lineOf,
  namedAccount,
  parseSearch,
  readLeniently,
  termField,
  terms,
  withField,
  withoutTerm,
  type AccountChoice,
  type SearchField,
  type Term,
} from './search-syntax.ts'

const ALL = '*'
/** The account select's choice of the open view's folders. */
const IN_VIEW = 'view'
const selectClass = 'h-9 w-full rounded-md border bg-transparent px-2 text-sm'
const TEXT_FIELDS = ['from', 'to', 'subject'] as const
const DATE_FIELDS = ['since', 'before'] as const

/**
 * The line a search in the URL is written as: its account and folder only when they are not the
 * scope's, which a line without them searches.
 */
function lineOfQuery(query: SearchQuery | undefined, accounts: Account[], scope: SearchScope): string {
  if (query === undefined) {
    return scope.line
  }
  const searched = searchedIn(query)
  // Beside a view's folders, any account and folder are written.
  const own = scope.view === null ? scope : null
  const { from, to, subject, text, since, before, unread, flagged, to_sort, has_attachment } = query
  return lineOf({
    from,
    to,
    subject,
    text,
    since,
    before,
    unread,
    flagged,
    to_sort,
    has_attachment,
    account: searched === null || searched.account === own?.account ? undefined : accountTerm(accounts, searched.account),
    folder: searched === null || searched.folder === own?.folder ? undefined : searched.folder,
  })
}

/**
 * A search over one account or all. The box takes the search syntax (`from:ada is:unread
 * invoice`), read as the user types: its terms show as chips, removed by a click, and "More
 * options" holds the same search as a form, every change written back into the line. Gmail's own
 * search sits only in the form. A line naming no account or folder searches where it was started
 * (`scope`), said beside the box, which widens it to all mail and back. A search shown can become a filter, where the instance runs them.
 * Remounted (by key) when the search in the URL changes, so it always shows the search the list
 * is showing. `compact`, the box and its buttons share one row, what else it shows under them.
 */
export function SearchForm({
  accounts,
  accountId,
  folder,
  scope: started,
  query,
  only,
  compact = false,
}: {
  accounts: Account[]
  accountId: AccountId
  folder: string
  /** Where a search starts: the folder or the view open; `null` until it is known, when none does. */
  scope: SearchScope | null
  query: SearchQuery | undefined
  /** The quick filters on: a search kept as a view keeps them as its own terms. */
  only?: readonly QuickFilter[] | undefined
  /** On one row with the box, the rest wrapping under it: with the reading pane below the list. */
  compact?: boolean
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const navigate = useNavigate()
  const filters = useFiltersOffered()
  const id = useId()
  const box = useRef<HTMLInputElement>(null)
  const scope = started ?? ALL_MAIL
  const [line, setLine] = useState(() => lineOfQuery(query, accounts, scope))
  const [gmailQuery, setGmailQuery] = useState(query?.gmail_query ?? '')
  const [expanded, setExpanded] = useState(
    query !== undefined && Object.keys(query).some((key) => key !== 'text' && key !== 'account' && key !== 'folder' && key !== 'view'),
  )

  const lexed = terms(line)
  const result = parseSearch(line)
  // A line at fault still fills the form: as far as it can be read.
  const shown = result.ok ? result.parsed : readLeniently(line)
  const choice: AccountChoice =
    shown.account === undefined ? { ok: true, account: scope.account } : namedAccount(accounts, shown.account)
  const searchedAccount = choice.ok ? choice.account : ''
  const searchedFolder = shown.folder ?? scope.folder
  // A view's folders, until the line names an account or a folder of its own.
  const inView = scope.view !== null && shown.account === undefined && shown.folder === undefined
  const inScope = scope.view === null ? searchedAccount === scope.account && searchedFolder === scope.folder : inView
  const everywhere = !inView && searchedAccount === ALL && searchedFolder === ALL
  const problem = !result.ok
    ? t(`search.errors.${result.code}`, { key: result.key, options: flagValues(result.key ?? '').join(', ') })
    : choice.ok
      ? null
      : t(`search.errors.${choice.code}`)

  function set(field: Exclude<SearchField, 'text'>, value: string | boolean | undefined): void {
    const next = withField(line, field, value)
    if (next !== null) {
      setLine(next)
    }
  }

  function submit(event: SubmitEvent<HTMLFormElement>): void {
    event.preventDefault()
    run(line)
  }

  /** Searches `asked`; a line at fault, or a scope not known yet, only takes the cursor to the box. */
  function run(asked: string): void {
    const read = parseSearch(asked)
    const account: AccountChoice | null = !read.ok
      ? null
      : read.parsed.account === undefined
        ? { ok: true, account: scope.account }
        : namedAccount(accounts, read.parsed.account)
    if (started === null || !read.ok || account?.ok !== true) {
      box.current?.focus()
      return
    }
    const { parsed } = read
    const q: SearchQuery =
      scope.view !== null && parsed.account === undefined && parsed.folder === undefined
        ? { view: scope.view.id }
        : { account: account.account, folder: parsed.folder ?? scope.folder }
    for (const key of ['text', ...TEXT_FIELDS, ...DATE_FIELDS] as const) {
      const value = parsed[key]?.trim()
      if (value) {
        q[key] = value
      }
    }
    if (gmailQuery.trim() !== '') {
      q.gmail_query = gmailQuery.trim()
    }
    for (const key of SEARCH_FLAG_FIELDS) {
      if (parsed[key] !== undefined) {
        q[key] = parsed[key]
      }
    }
    void navigate({
      to: '/mail',
      search: (previous) => ({ account: previous.account, folder: previous.folder, sub: previous.sub, view: previous.view, q }),
    })
  }

  /** Searches the line in another account and folder: the scope's own when not given. */
  function runIn(account: string | undefined, searched: string | undefined): void {
    const moved = withField(line, 'account', account)
    const next = moved === null ? null : withField(moved, 'folder', searched)
    if (next === null) {
      box.current?.focus()
      return
    }
    setLine(next)
    run(next)
  }

  /** Where an account and a folder search, named. */
  function placeName(account: string, searched: string): string {
    if (account === ALL && searched === ALL) {
      return t('search.allMail')
    }
    if (account === ALL && searched.toUpperCase() === 'INBOX') {
      return t('views.allInboxes')
    }
    const name = folderLabel(searched)
    const place =
      scope.subfolders && account === scope.account && searched === scope.folder ? t('search.withSubfolders', { folder: name }) : name
    const of = account === ALL ? t('search.accountAll') : accounts.find((each) => String(each.id) === account)?.display_name
    return of === undefined || accounts.length < 2 ? place : t('search.place', { folder: place, account: of })
  }

  function folderLabel(path: string): string {
    return path === ALL ? t('search.folderAll') : path.toUpperCase() === 'INBOX' ? t('search.folderInbox') : path
  }

  /** Back to the open view's folders: neither an account nor a folder named. */
  function chooseView(): void {
    const next = withField(line, 'account', undefined)
    const kept = next === null ? null : withField(next, 'folder', undefined)
    if (kept !== null) {
      setLine(kept)
    }
  }

  /** What a chip says: the criterion and its value, in the form's words. */
  function chipLabel(term: Term): string {
    const field = termField(term)
    const raw = line.slice(term.start, term.end)
    if (field === null) {
      return raw
    }
    if (field === 'text') {
      return term.value
    }
    if ((SEARCH_FLAG_FIELDS as readonly string[]).includes(field)) {
      return field === 'unread' && term.value.toLowerCase() === 'read' ? t('search.read') : t(`search.${field}`)
    }
    let value = term.value
    if (field === 'folder') {
      value = folderLabel(value)
    } else if (field === 'account') {
      const named = namedAccount(accounts, value)
      value = !named.ok
        ? value
        : named.account === ALL
          ? t('search.accountAll')
          : (accounts.find((account) => String(account.id) === named.account)?.display_name ?? value)
    }
    return t('search.chip', { name: t(`search.${field}`), value })
  }

  // The folder shown is offered, next to the inbox and every folder; in the account shown, it
  // takes its subfolders along.
  const shownLabel = folderLabel(scope.folder)
  const withSubfolders = scope.subfolders && searchedAccount === scope.account
  const folderOptions = [
    ...(scope.folder.toUpperCase() === 'INBOX' ? [] : [['INBOX', t('search.folderInbox')]]),
    ...(scope.folder !== ALL ? [[scope.folder, withSubfolders ? t('search.withSubfolders', { folder: shownLabel }) : shownLabel]] : []),
    [ALL, t('search.folderAll')],
  ]
  if (!folderOptions.some(([value]) => value === searchedFolder)) {
    folderOptions.push([searchedFolder, searchedFolder])
  }

  const optionsId = `${id}-options`
  const problemId = `${id}-problem`
  const chips = lexed.terms.filter((term) => term.key !== null || term.value !== '')
  // A quote left open hides where the line's terms end: the form waits for it to be closed.
  const locked = lexed.unterminated
  const viewName = scope.view?.name ?? t('views.allInboxes')
  const scopeName =
    scope.view !== null ? (scope.view.name ?? t('views.allInboxesSubfolders')) : placeName(scope.account, scope.folder)
  const searchedName = inView ? scopeName : placeName(searchedAccount, searchedFolder)
  // Widening what is asked for: nothing is, with the box empty and no search shown.
  const asks = query !== undefined || line.trim() !== '' || gmailQuery.trim() !== ''
  // Under the box, or beside it when compact.
  const buttons = (
      <div className="flex flex-wrap items-center gap-2">
        {started !== null && choice.ok && (
          <p className="min-w-0 text-xs break-words text-muted-foreground">{t('search.scope', { scope: searchedName })}</p>
        )}
        {started !== null && asks && !everywhere && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            onClick={() => {
              runIn(scope.view === null && scope.account === ALL ? undefined : ALL, scope.view === null && scope.folder === ALL ? undefined : ALL)
            }}
          >
            {t('search.searchAllMail')}
          </Button>
        )}
        {started !== null && asks && !inScope && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            className="max-w-full"
            onClick={() => {
              runIn(undefined, undefined)
            }}
          >
            <span className="min-w-0 truncate">{t('search.backTo', { scope: scopeName })}</span>
          </Button>
        )}
        <Button
          type="button"
          variant="ghost"
          size="xs"
          aria-expanded={expanded}
          aria-controls={optionsId}
          onClick={() => {
            setExpanded((value) => !value)
          }}
        >
          {expanded ? t('search.fewerOptions') : t('search.moreOptions')}
        </Button>
        {query !== undefined && filters && (
          <Link to="/filters/new" search={{ q: query }} className={buttonVariants({ variant: 'ghost', size: 'xs', className: 'ml-auto' })}>
            <ListFilter aria-hidden="true" />
            {t('search.createFilter')}
          </Link>
        )}
        {query !== undefined && (
          // What the list shows: the search as narrowed by the quick filters on.
          <SaveViewButton query={listQuery(accountId, folder, query, only) ?? query} className={filters ? undefined : 'ml-auto'} />
        )}
        {query !== undefined && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            onClick={() => {
              void navigate({ to: '/mail', search: (previous) => ({ account: accountId, folder, sub: previous.sub, view: previous.view }) })
            }}
          >
            {t('search.clear')}
          </Button>
        )}
      </div>
  )
  return (
    <form role="search" aria-label={t('search.label')} onSubmit={submit}
      className={cn('flex gap-2', compact ? 'flex-wrap items-center' : 'flex-col')}
    >
      <div className={cn('flex gap-2', compact && 'min-w-48 flex-1')}>
        <Input
          ref={box}
          type="search"
          name="q"
          aria-label={t('search.text')}
          placeholder={t('search.placeholder')}
          autoComplete="off"
          spellCheck={false}
          value={line}
          aria-invalid={problem !== null}
          aria-describedby={problem === null ? undefined : problemId}
          onChange={(event) => {
            setLine(event.target.value)
          }}
        />
        <Button type="submit" variant="outline" size="icon" aria-label={t('search.submit')}>
          <Search aria-hidden="true" />
        </Button>
      </div>
      {compact && buttons}
      {problem !== null && (
        <p id={problemId} className={cn('text-xs text-destructive', compact && 'basis-full')}>
          {problem}
        </p>
      )}
      {chips.length > 0 && (
        <ul aria-label={t('search.terms')} className={cn('flex flex-wrap gap-1', compact && 'basis-full')}>
          {chips.map((term) => {
            const label = chipLabel(term)
            return (
              <li key={term.start} className="flex max-w-full items-center gap-1 rounded-md border py-0.5 pr-0.5 pl-2 text-xs">
                <span className="min-w-0 truncate">{label}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  aria-label={t('search.removeTerm', { term: label })}
                  onClick={() => {
                    setLine(withoutTerm(line, term))
                  }}
                >
                  <X aria-hidden="true" />
                </Button>
              </li>
            )
          })}
        </ul>
      )}
      {!compact && buttons}
      <div id={optionsId} hidden={!expanded} className={cn('grid grid-cols-2 gap-2', compact && 'basis-full')}>
        <Field id={`${id}-account`} label={t('search.account')}>
          <select
            id={`${id}-account`}
            className={selectClass}
            value={inView ? IN_VIEW : searchedAccount}
            disabled={locked}
            onChange={(event) => {
              const { value } = event.target
              if (value === IN_VIEW) {
                chooseView()
              } else {
                set('account', scope.view === null && value === scope.account ? undefined : accountTerm(accounts, value))
              }
            }}
          >
            {scope.view !== null && <option value={IN_VIEW}>{t('search.viewAccounts', { view: viewName })}</option>}
            {!choice.ok && (
              <option value="" disabled>
                {shown.account}
              </option>
            )}
            <option value={ALL}>{t('search.accountAll')}</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.display_name}
              </option>
            ))}
          </select>
        </Field>
        <Field id={`${id}-folder`} label={t('search.folder')}>
          <select
            id={`${id}-folder`}
            className={selectClass}
            value={inView ? '' : searchedFolder}
            disabled={locked}
            onChange={(event) => {
              const { value } = event.target
              if (value === '') {
                chooseView()
              } else {
                set('folder', scope.view === null && value === scope.folder ? undefined : value)
              }
            }}
          >
            {/* No folder's path is empty. */}
            {scope.view !== null && <option value="">{t('search.viewFolders', { view: viewName })}</option>}
            {folderOptions.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        {TEXT_FIELDS.map((key) => (
          <Field key={key} id={`${id}-${key}`} label={t(`search.${key}`)} wide={key === 'subject'}>
            <Input
              id={`${id}-${key}`}
              value={shown[key] ?? ''}
              disabled={locked}
              onChange={(event) => {
                set(key, event.target.value)
              }}
            />
          </Field>
        ))}
        {DATE_FIELDS.map((key) => (
          <Field key={key} id={`${id}-${key}`} label={t(`search.${key}`)}>
            <Input
              id={`${id}-${key}`}
              type="date"
              value={shown[key] ?? ''}
              disabled={locked}
              onChange={(event) => {
                set(key, event.target.value)
              }}
            />
          </Field>
        ))}
        <fieldset className="col-span-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">
          <legend className="sr-only">{t('search.flags')}</legend>
          {SEARCH_FLAG_FIELDS.map((key) => (
            <span key={key} className="flex items-center gap-2">
              <input
                id={`${id}-${key}`}
                type="checkbox"
                className="size-4 accent-primary"
                checked={shown[key] === true}
                disabled={locked}
                onChange={(event) => {
                  set(key, event.target.checked ? true : undefined)
                }}
              />
              <label htmlFor={`${id}-${key}`}>{t(`search.${key}`)}</label>
            </span>
          ))}
        </fieldset>
        <Field id={`${id}-gmail`} label={t('search.gmail_query')} hint={t('search.gmailHint')} wide>
          <Input
            id={`${id}-gmail`}
            aria-describedby={`${id}-gmail-hint`}
            value={gmailQuery}
            onChange={(event) => {
              setGmailQuery(event.target.value)
            }}
          />
        </Field>
      </div>
    </form>
  )
}

function Field({
  id,
  label,
  hint,
  wide = false,
  children,
}: {
  id: string
  label: string
  hint?: string
  wide?: boolean
  children: ReactElement
}): ReactElement {
  return (
    <div className={wide ? 'col-span-2 flex flex-col gap-1' : 'flex flex-col gap-1'}>
      <label htmlFor={id} className="text-xs font-medium">
        {label}
      </label>
      {children}
      {hint !== undefined && (
        <p id={`${id}-hint`} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
    </div>
  )
}

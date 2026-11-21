import { useId, useRef, useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from '@tanstack/react-router'
import { ListFilter, Search, X } from 'lucide-react'

import type { AccountId, Account } from '@/api/accounts.ts'
import { SEARCH_FLAG_FIELDS, type SearchQuery } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { buttonVariants } from '@/components/ui/button-variants.ts'
import { Input } from '@/components/ui/input.tsx'

import { useFiltersOffered } from './filter-draft.ts'
import { SaveViewButton } from './saved-views.tsx'
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
const selectClass = 'h-9 w-full rounded-md border bg-transparent px-2 text-sm'
const TEXT_FIELDS = ['from', 'to', 'subject'] as const
const DATE_FIELDS = ['since', 'before'] as const

/**
 * The line a search in the URL is written as: its account and folder only when they are not the
 * ones shown, which a line without them searches.
 */
function lineOfQuery(query: SearchQuery | undefined, accounts: Account[], accountId: AccountId, folder: string): string {
  if (query === undefined) {
    return ''
  }
  const { account, folder: searched, from, to, subject, text, since, before, unread, flagged, to_sort, has_attachment } = query
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
    account: account === undefined || account === String(accountId) ? undefined : accountTerm(accounts, account),
    folder: searched === folder ? undefined : searched,
  })
}

/**
 * A search over one account or all. The box takes the search syntax (`from:ada is:unread
 * invoice`), read as the user types: its terms show as chips, removed by a click, and "More
 * options" holds the same search as a form, every change written back into the line. Gmail's own
 * search sits only in the form. A search shown can become a filter, where the instance runs them.
 * Remounted (by key) when the search in the URL changes, so it always shows the search the list
 * is showing.
 */
export function SearchForm({
  accounts,
  accountId,
  folder,
  subfolders,
  query,
  only,
}: {
  accounts: Account[]
  accountId: AccountId
  folder: string
  /** The folder shown is listed with its subfolders: a search in it reads them too. */
  subfolders: boolean
  query: SearchQuery | undefined
  /** The quick filters on: a search kept as a view keeps them as its own terms. */
  only?: readonly QuickFilter[] | undefined
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const navigate = useNavigate()
  const filters = useFiltersOffered()
  const id = useId()
  const box = useRef<HTMLInputElement>(null)
  const [line, setLine] = useState(() => lineOfQuery(query, accounts, accountId, folder))
  const [gmailQuery, setGmailQuery] = useState(query?.gmail_query ?? '')
  const [expanded, setExpanded] = useState(
    query !== undefined && Object.keys(query).some((key) => key !== 'text' && key !== 'account' && key !== 'folder'),
  )

  const lexed = terms(line)
  const result = parseSearch(line)
  // A line at fault still fills the form: as far as it can be read.
  const shown = result.ok ? result.parsed : readLeniently(line)
  const choice: AccountChoice =
    shown.account === undefined ? { ok: true, account: String(accountId) } : namedAccount(accounts, shown.account)
  const searchedAccount = choice.ok ? choice.account : ''
  const searchedFolder = shown.folder ?? folder
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
    if (!result.ok || !choice.ok) {
      box.current?.focus()
      return
    }
    const { parsed } = result
    const q: SearchQuery = { account: searchedAccount, folder: searchedFolder }
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
      search: (previous) => ({ account: previous.account, folder: previous.folder, sub: previous.sub, q }),
    })
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
      value = value === ALL ? t('search.folderAll') : value.toUpperCase() === 'INBOX' ? t('search.folderInbox') : value
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
  const shownLabel = folder.toUpperCase() === 'INBOX' ? t('search.folderInbox') : folder
  const withSubfolders = subfolders && searchedAccount === String(accountId)
  const folderOptions = [
    ...(folder.toUpperCase() === 'INBOX' ? [] : [['INBOX', t('search.folderInbox')]]),
    ...(folder !== ALL ? [[folder, withSubfolders ? t('search.withSubfolders', { folder: shownLabel }) : shownLabel]] : []),
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
  return (
    <form role="search" aria-label={t('search.label')} onSubmit={submit} className="flex flex-col gap-2">
      <div className="flex gap-2">
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
      {problem !== null && (
        <p id={problemId} className="text-xs text-destructive">
          {problem}
        </p>
      )}
      {chips.length > 0 && (
        <ul aria-label={t('search.terms')} className="flex flex-wrap gap-1">
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
      <div className="flex flex-wrap items-center gap-2">
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
              void navigate({ to: '/mail', search: (previous) => ({ account: accountId, folder, sub: previous.sub }) })
            }}
          >
            {t('search.clear')}
          </Button>
        )}
      </div>
      <div id={optionsId} hidden={!expanded} className="grid grid-cols-2 gap-2">
        <Field id={`${id}-account`} label={t('search.account')}>
          <select
            id={`${id}-account`}
            className={selectClass}
            value={searchedAccount}
            disabled={locked}
            onChange={(event) => {
              const { value } = event.target
              set('account', value === String(accountId) ? undefined : accountTerm(accounts, value))
            }}
          >
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
            value={searchedFolder}
            disabled={locked}
            onChange={(event) => {
              const { value } = event.target
              set('folder', value === folder ? undefined : value)
            }}
          >
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

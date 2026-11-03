import { useId, useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from '@tanstack/react-router'
import { Search } from 'lucide-react'

import { SEARCH_FLAG_FIELDS, type SearchQuery } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'

import { MAIL_NS } from './i18n.ts'

const ALL = '*'
const selectClass = 'h-9 w-full rounded-md border bg-transparent px-2 text-sm'
const TEXT_FIELDS = ['from', 'to', 'subject'] as const
const DATE_FIELDS = ['since', 'before'] as const

interface Draft {
  account: string
  folder: string
  text: string
  from: string
  to: string
  subject: string
  since: string
  before: string
  gmail_query: string
  unread: boolean
  flagged: boolean
  has_attachment: boolean
}

/** A new search starts where the reader is: the account and folder shown. */
function draftOf(query: SearchQuery | undefined, accountId: string, folder: string): Draft {
  return {
    account: query?.account ?? accountId,
    folder: query?.folder ?? folder,
    text: query?.text ?? '',
    from: query?.from ?? '',
    to: query?.to ?? '',
    subject: query?.subject ?? '',
    since: query?.since ?? '',
    before: query?.before ?? '',
    gmail_query: query?.gmail_query ?? '',
    unread: query?.unread ?? false,
    flagged: query?.flagged ?? false,
    has_attachment: query?.has_attachment ?? false,
  }
}

function queryOf(draft: Draft): SearchQuery {
  const query: SearchQuery = { account: draft.account, folder: draft.folder }
  for (const key of ['text', ...TEXT_FIELDS, ...DATE_FIELDS, 'gmail_query'] as const) {
    const value = draft[key].trim()
    if (value !== '') {
      query[key] = value
    }
  }
  for (const key of SEARCH_FLAG_FIELDS) {
    if (draft[key]) {
      query[key] = true
    }
  }
  return query
}

/**
 * A search over one account or all, with mailroom-mcp's search fields; the words box alone
 * searches headers and body, the rest are behind "More options". Remounted (by key) when the
 * search in the URL changes, so it always shows the search the list is showing.
 */
export function SearchForm({
  accounts,
  accountId,
  folder,
  query,
}: {
  accounts: string[]
  accountId: string
  folder: string
  query: SearchQuery | undefined
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const navigate = useNavigate()
  const id = useId()
  const [draft, setDraft] = useState(() => draftOf(query, accountId, folder))
  const [expanded, setExpanded] = useState(
    query !== undefined && Object.keys(query).some((key) => key !== 'text' && key !== 'account' && key !== 'folder'),
  )

  function set<K extends keyof Draft>(key: K, value: Draft[K]): void {
    setDraft((previous) => ({ ...previous, [key]: value }))
  }

  function submit(event: SubmitEvent<HTMLFormElement>): void {
    event.preventDefault()
    void navigate({
      to: '/mail',
      search: (previous) => ({ account: previous.account, folder: previous.folder, q: queryOf(draft) }),
    })
  }

  // The folder shown is offered, next to the inbox and every folder.
  const folderOptions = [
    ['INBOX', t('search.folderInbox')],
    ...(folder !== 'INBOX' && folder !== ALL ? [[folder, folder]] : []),
    [ALL, t('search.folderAll')],
  ]
  if (!folderOptions.some(([value]) => value === draft.folder)) {
    folderOptions.push([draft.folder, draft.folder])
  }

  const optionsId = `${id}-options`
  return (
    <form role="search" aria-label={t('search.label')} onSubmit={submit} className="flex flex-col gap-2">
      <div className="flex gap-2">
        <Input
          type="search"
          aria-label={t('search.text')}
          placeholder={t('search.placeholder')}
          value={draft.text}
          onChange={(event) => {
            set('text', event.target.value)
          }}
        />
        <Button type="submit" variant="outline" size="icon" aria-label={t('search.submit')}>
          <Search aria-hidden="true" />
        </Button>
      </div>
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
        {query !== undefined && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            className="ml-auto"
            onClick={() => {
              void navigate({ to: '/mail', search: { account: accountId, folder } })
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
            value={draft.account}
            onChange={(event) => {
              set('account', event.target.value)
            }}
          >
            <option value={ALL}>{t('search.accountAll')}</option>
            {accounts.map((account) => (
              <option key={account} value={account}>
                {account}
              </option>
            ))}
          </select>
        </Field>
        <Field id={`${id}-folder`} label={t('search.folder')}>
          <select
            id={`${id}-folder`}
            className={selectClass}
            value={draft.folder}
            onChange={(event) => {
              set('folder', event.target.value)
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
              value={draft[key]}
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
              value={draft[key]}
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
                checked={draft[key]}
                onChange={(event) => {
                  set(key, event.target.checked)
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
            value={draft.gmail_query}
            onChange={(event) => {
              set('gmail_query', event.target.value)
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

import { useId, useState, type ReactElement, type ReactNode, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'

import {
  SEND_MODES,
  TLS_MODES,
  accountIssues,
  type Account,
  type AccountFields,
  type AccountIssue,
  type Endpoint,
} from '@/api/accounts.ts'
import { ApiRequestError } from '@/api/http.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'
import { accountErrorMessage } from '@/accounts/messages.ts'

type Field =
  | 'id'
  | 'login'
  | 'password'
  | 'address'
  | 'name'
  | 'imap.host'
  | 'imap.port'
  | 'imap.tls'
  | 'smtp.host'
  | 'smtp.port'
  | 'smtp.tls'
  | 'send_mode'

/** Where mailroom-core's issue belongs in this form; `null` for the account as a whole. */
function fieldOf(issue: AccountIssue): Field | null {
  const [first, second, third] = issue.path
  switch (first) {
    case 'id':
    case 'login':
    case 'password':
    case 'send_mode':
      return first
    case 'imap':
    case 'smtp':
      return second === 'host' || second === 'port' || second === 'tls' ? `${first}.${second}` : null
    case 'identities':
      // The form edits the first identity; the account's others are kept as they are.
      if (second === undefined || second === 0) {
        return third === 'name' ? 'name' : 'address'
      }
      return null
    default:
      return null
  }
}

interface FormState {
  id: string
  login: string
  password: string
  address: string
  name: string
  imapHost: string
  imapPort: string
  imapTls: Endpoint['tls']
  smtpHost: string
  smtpPort: string
  smtpTls: Endpoint['tls']
  sendMode: AccountFields['send_mode']
}

function initialState(account: Account | undefined): FormState {
  const identity = account?.identities[0]
  return {
    id: account?.id ?? '',
    login: account?.login ?? '',
    password: '',
    address: identity?.address ?? '',
    name: identity?.name ?? '',
    imapHost: account?.imap.host ?? '',
    imapPort: String(account?.imap.port ?? 993),
    imapTls: account?.imap.tls ?? 'implicit',
    smtpHost: account?.smtp.host ?? '',
    smtpPort: String(account?.smtp.port ?? 465),
    smtpTls: account?.smtp.tls ?? 'implicit',
    sendMode: account?.send_mode ?? 'drafts',
  }
}

function fieldsOf(state: FormState, account: Account | undefined): AccountFields {
  const name = state.name.trim()
  const first = { ...account?.identities[0], address: state.address.trim(), name: name === '' ? null : name }
  return {
    login: state.login.trim(),
    send_mode: state.sendMode,
    imap: { host: state.imapHost.trim(), port: Number(state.imapPort), tls: state.imapTls },
    smtp: { host: state.smtpHost.trim(), port: Number(state.smtpPort), tls: state.smtpTls },
    identities: [first, ...(account?.identities.slice(1) ?? [])],
    auto_approve: account?.auto_approve ?? [],
    ...(state.password === '' ? {} : { password: state.password }),
  }
}

/**
 * Adds an account (no `account`) or edits one: its id stays, and an empty password keeps the
 * stored one. `save` sends it; a refusal shows next to the fields mailroom-core found wrong.
 */
export function AccountForm({
  account,
  save,
  onCancel,
}: {
  account?: Account
  save: (id: string, fields: AccountFields) => Promise<unknown>
  onCancel: () => void
}): ReactElement {
  const { t } = useTranslation()
  const prefix = useId()
  const editing = account !== undefined
  const [state, setState] = useState(() => initialState(account))
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [issues, setIssues] = useState<Partial<Record<Field, string>>>({})

  function set<K extends keyof FormState>(key: K, value: FormState[K]): void {
    setState((current) => ({ ...current, [key]: value }))
  }

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    setIssues({})
    try {
      await save(state.id.trim(), fieldsOf(state, account))
    } catch (failure) {
      const byField: Partial<Record<Field, string>> = {}
      const general: string[] = []
      if (failure instanceof ApiRequestError) {
        for (const issue of accountIssues(failure.body)) {
          const field = fieldOf(issue)
          if (field === null) {
            general.push(issue.message)
          } else {
            byField[field] = byField[field] === undefined ? issue.message : `${byField[field]} ${issue.message}`
          }
        }
      }
      setIssues(byField)
      setError([accountErrorMessage(t, failure, t('accounts.saveFailed')), ...general].join(' '))
      setSubmitting(false)
    }
  }

  const errorId = `${prefix}-error`
  const titleId = `${prefix}-title`
  /** The input's id and state; its description is the row's hint (`hinted`) and issue. */
  const control = (
    field: Field,
    hinted = false,
  ): { id: string; 'aria-invalid': boolean; 'aria-describedby'?: string } => {
    const id = `${prefix}-${field}`
    const invalid = issues[field] !== undefined
    const described = [hinted ? `${id}-hint` : null, invalid ? `${id}-issue` : null].filter(
      (part) => part !== null,
    )
    return {
      id,
      'aria-invalid': invalid,
      'aria-describedby': described.length === 0 ? undefined : described.join(' '),
    }
  }
  const row = (field: Field, label: string, input: ReactNode, hint?: string): ReactElement => (
    <div className="flex flex-col gap-1">
      <label htmlFor={`${prefix}-${field}`} className="text-sm font-medium">
        {label}
      </label>
      {input}
      {hint !== undefined && (
        <p id={`${prefix}-${field}-hint`} className="text-sm text-muted-foreground">
          {hint}
        </p>
      )}
      {issues[field] !== undefined && (
        <p id={`${prefix}-${field}-issue`} className="text-sm text-destructive">
          {issues[field]}
        </p>
      )}
    </div>
  )
  const endpoint = (kind: 'imap' | 'smtp'): ReactElement => {
    const host = kind === 'imap' ? 'imapHost' : 'smtpHost'
    const port = kind === 'imap' ? 'imapPort' : 'smtpPort'
    const tls = kind === 'imap' ? 'imapTls' : 'smtpTls'
    return (
      <fieldset className="grid gap-3 rounded-md border p-3 sm:grid-cols-[2fr_1fr_1fr]">
        <legend className="px-1 text-sm font-medium">{t(`accounts.${kind}`)}</legend>
        {row(
          `${kind}.host`,
          t(`accounts.${kind}Host`),
          <Input
            {...control(`${kind}.host`)}
            required
            maxLength={255}
            autoCapitalize="none"
            spellCheck={false}
            value={state[host]}
            onChange={(event) => {
              set(host, event.target.value)
            }}
          />,
        )}
        {row(
          `${kind}.port`,
          t(`accounts.${kind}Port`),
          <Input
            {...control(`${kind}.port`)}
            type="number"
            required
            min={1}
            max={65535}
            value={state[port]}
            onChange={(event) => {
              set(port, event.target.value)
            }}
          />,
        )}
        {row(
          `${kind}.tls`,
          t(`accounts.${kind}Tls`),
          <select
            {...control(`${kind}.tls`)}
            className="h-9 rounded-md border bg-transparent px-2 text-sm"
            value={state[tls]}
            onChange={(event) => {
              set(tls, event.target.value as Endpoint['tls'])
            }}
          >
            {TLS_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {t(`accounts.tls.${mode}`)}
              </option>
            ))}
          </select>,
        )}
      </fieldset>
    )
  }

  return (
    <form
      aria-labelledby={titleId}
      aria-describedby={error === null ? undefined : errorId}
      className="flex flex-col gap-3 rounded-lg border p-4"
      onSubmit={(event) => void submit(event)}
    >
      <h2 id={titleId} className="font-medium">
        {editing ? t('accounts.editTitle', { id: account.id }) : t('accounts.add')}
      </h2>
      {!editing &&
        row(
          'id',
          t('accounts.id'),
          <Input
            {...control('id', true)}
            required
            maxLength={64}
            autoCapitalize="none"
            spellCheck={false}
            value={state.id}
            onChange={(event) => {
              set('id', event.target.value)
            }}
          />,
          t('accounts.idHint'),
        )}
      {row(
        'address',
        t('accounts.address'),
        <Input
          {...control('address')}
          type="email"
          required
          maxLength={320}
          value={state.address}
          onChange={(event) => {
            set('address', event.target.value)
          }}
        />,
      )}
      {row(
        'name',
        t('accounts.name'),
        <Input
          {...control('name', true)}
          maxLength={200}
          value={state.name}
          onChange={(event) => {
            set('name', event.target.value)
          }}
        />,
        t('accounts.nameHint'),
      )}
      {row(
        'login',
        t('accounts.login'),
        <Input
          {...control('login')}
          required
          maxLength={320}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          value={state.login}
          onChange={(event) => {
            set('login', event.target.value)
          }}
        />,
      )}
      {row(
        'password',
        t('accounts.password'),
        <Input
          {...control('password', true)}
          type="password"
          autoComplete="new-password"
          required={!editing}
          maxLength={4096}
          value={state.password}
          onChange={(event) => {
            set('password', event.target.value)
          }}
        />,
        editing ? t('accounts.passwordKeep') : t('accounts.passwordHint'),
      )}
      {endpoint('imap')}
      {endpoint('smtp')}
      {row(
        'send_mode',
        t('accounts.sendMode'),
        <select
          {...control('send_mode', true)}
          className="h-9 rounded-md border bg-transparent px-2 text-sm"
          value={state.sendMode}
          onChange={(event) => {
            set('sendMode', event.target.value as AccountFields['send_mode'])
          }}
        >
          {SEND_MODES.map((mode) => (
            <option key={mode} value={mode}>
              {t(`accounts.sendModes.${mode}`)}
            </option>
          ))}
        </select>,
        t('accounts.sendModeHint'),
      )}
      {error !== null && (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={submitting}>
          {editing ? t('accounts.save') : t('accounts.addButton')}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          {t('accounts.cancel')}
        </Button>
      </div>
    </form>
  )
}

import { useEffect, useId, useRef, useState, type ReactElement, type ReactNode, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'

import {
  SEND_MODES,
  TLS_MODES,
  type Account,
  type AccountFields,
  type Endpoint,
} from '@/api/accounts.ts'
import { ApiRequestError, refusalIssues, type RefusalIssue } from '@/api/http.ts'
import { useMailLayer } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'
import { accountErrorMessage } from '@/accounts/messages.ts'

/** The most identities an account may have, as the API takes them. */
const MAX_IDENTITIES = 50

type Field =
  | 'id'
  | 'login'
  | 'password'
  | `identities.${number}.address`
  | `identities.${number}.name`
  | 'default'
  | 'imap.host'
  | 'imap.port'
  | 'imap.tls'
  | 'smtp.host'
  | 'smtp.port'
  | 'smtp.tls'
  | 'send_mode'

function identityField(index: number, key: 'address' | 'name'): Field {
  return `identities.${String(index)}.${key}` as Field
}

/**
 * Where mailroom-core's issue belongs in this form (with `identities` of them); `null` for the
 * account as a whole.
 */
function fieldOf(issue: RefusalIssue, identities: number): Field | null {
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
      // About them all (which one is the default, shown with several), else about one of them.
      if (typeof second !== 'number') {
        return identities > 1 ? 'default' : null
      }
      return identityField(second, third === 'name' ? 'name' : 'address')
    default:
      return null
  }
}

/** One identity as typed; `key` stays with it while others are added or removed. */
interface IdentityDraft {
  key: number
  address: string
  name: string
}

interface FormState {
  id: string
  login: string
  password: string
  identities: IdentityDraft[]
  /** The index of the default identity, when there is more than one. */
  defaultIdentity: number
  imapHost: string
  imapPort: string
  imapTls: Endpoint['tls']
  smtpHost: string
  smtpPort: string
  smtpTls: Endpoint['tls']
  sendMode: AccountFields['send_mode']
}

function initialState(account: Account | undefined): FormState {
  const stored = account?.identities ?? []
  const identities = stored.map((identity, key) => ({
    key,
    address: identity.address,
    name: identity.name ?? '',
  }))
  return {
    id: account?.id ?? '',
    login: account?.login ?? '',
    password: '',
    identities: identities.length === 0 ? [{ key: 0, address: '', name: '' }] : identities,
    defaultIdentity: Math.max(
      stored.findIndex((identity) => identity.default === true),
      0,
    ),
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
  const many = state.identities.length > 1
  const identities = state.identities.map((identity, index) => {
    const name = identity.name.trim()
    return {
      address: identity.address.trim(),
      name: name === '' ? null : name,
      ...(many ? { default: index === state.defaultIdentity } : {}),
    }
  })
  return {
    login: state.login.trim(),
    send_mode: state.sendMode,
    imap: { host: state.imapHost.trim(), port: Number(state.imapPort), tls: state.imapTls },
    smtp: { host: state.smtpHost.trim(), port: Number(state.smtpPort), tls: state.smtpTls },
    identities,
    auto_approve: account?.auto_approve ?? [],
    ...(state.password === '' ? {} : { password: state.password }),
  }
}

/**
 * Adds an account (no `account`) or edits one: its id stays, and an empty password keeps the
 * stored one. Every identity is edited here: the first, more added or removed, and which one is
 * the default once there are several. `save` sends it; a refusal shows next to the fields
 * mailroom-core found wrong. The send mode shows only with the mail client or MCP; otherwise the
 * account keeps its own, and a new one gets `drafts`.
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
  const sends = useMailLayer()
  const prefix = useId()
  const editing = account !== undefined
  const [state, setState] = useState(() => initialState(account))
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [issues, setIssues] = useState<Partial<Record<Field, string>>>({})
  const nextIdentityKey = useRef(state.identities.length)
  const addButton = useRef<HTMLButtonElement>(null)
  const focusAdd = useRef(false)

  function set<K extends keyof FormState>(key: K, value: FormState[K]): void {
    setState((current) => ({ ...current, [key]: value }))
  }

  function setIdentity(index: number, key: 'address' | 'name', value: string): void {
    setState((current) => ({
      ...current,
      identities: current.identities.map((identity, at) => (at === index ? { ...identity, [key]: value } : identity)),
    }))
  }

  function addIdentity(): void {
    const key = nextIdentityKey.current
    nextIdentityKey.current += 1
    set('identities', [...state.identities, { key, address: '', name: '' }])
  }

  /**
   * Removes one, with every identity's issues (their places move), and gives focus to Add another
   * address.
   */
  function removeIdentity(index: number): void {
    setState((current) => {
      const chosen = current.defaultIdentity
      return {
        ...current,
        identities: current.identities.filter((_, at) => at !== index),
        defaultIdentity: chosen === index ? 0 : chosen > index ? chosen - 1 : chosen,
      }
    })
    setIssues((current) =>
      Object.fromEntries(
        Object.entries(current).filter(([field]) => !field.startsWith('identities.') && field !== 'default'),
      ),
    )
    focusAdd.current = true
  }

  useEffect(() => {
    if (focusAdd.current) {
      focusAdd.current = false
      addButton.current?.focus()
    }
  }, [state.identities])

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
        for (const issue of refusalIssues(failure.body)) {
          const field = fieldOf(issue, state.identities.length)
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
      {state.identities.map((identity, index) => {
        const address = identityField(index, 'address')
        const name = identityField(index, 'name')
        const first = index === 0
        const fields = (
          <>
            {row(
              address,
              first ? t('accounts.address') : t('accounts.otherAddress', { n: index }),
              <Input
                {...control(address)}
                type="email"
                required
                maxLength={320}
                value={identity.address}
                onChange={(event) => {
                  setIdentity(index, 'address', event.target.value)
                }}
              />,
            )}
            {row(
              name,
              first ? t('accounts.name') : t('accounts.otherName', { n: index }),
              <Input
                {...control(name, first)}
                maxLength={200}
                value={identity.name}
                onChange={(event) => {
                  setIdentity(index, 'name', event.target.value)
                }}
              />,
              first ? t('accounts.nameHint') : undefined,
            )}
          </>
        )
        if (first) {
          return (
            <div key={identity.key} className="flex flex-col gap-3">
              {fields}
            </div>
          )
        }
        return (
          <div key={identity.key} className="flex flex-col gap-3 rounded-md border p-3">
            {fields}
            <Button
              type="button"
              variant="ghost"
              className="self-start"
              onClick={() => {
                removeIdentity(index)
              }}
            >
              {t('accounts.removeAddress', { n: index })}
            </Button>
          </div>
        )
      })}
      {state.identities.length < MAX_IDENTITIES && (
        <Button ref={addButton} type="button" variant="outline" className="self-start" onClick={addIdentity}>
          {t('accounts.addAddress')}
        </Button>
      )}
      {state.identities.length > 1 &&
        row(
          'default',
          t('accounts.defaultAddress'),
          <select
            {...control('default', true)}
            className="h-9 rounded-md border bg-transparent px-2 text-sm"
            value={state.defaultIdentity}
            onChange={(event) => {
              set('defaultIdentity', Number(event.target.value))
            }}
          >
            {state.identities.map((identity, index) => (
              <option key={identity.key} value={index}>
                {identity.address.trim() === ''
                  ? index === 0
                    ? t('accounts.address')
                    : t('accounts.otherAddress', { n: index })
                  : identity.address.trim()}
              </option>
            ))}
          </select>,
          t('accounts.defaultAddressHint'),
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
      {sends &&
        row(
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

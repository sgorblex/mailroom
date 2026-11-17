import { useEffect, useId, useRef, useState, type ReactElement, type ReactNode, type SubmitEvent } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { Ban } from 'lucide-react'

import {
  ACCOUNT_COLOURS,
  SEND_MODES,
  TLS_MODES,
  findSettings,
  type Account,
  type AccountColour,
  type AccountFields,
  type Endpoint,
  type Suggestion,
} from '@/api/accounts.ts'
import { ApiRequestError, refusalIssues, type RefusalIssue } from '@/api/http.ts'
import { useMailLayer } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'
import { AccountDot } from '@/accounts/account-name.tsx'
import { accountErrorMessage } from '@/accounts/messages.ts'
import { PRESETS } from '@/accounts/presets.ts'

/** The most identities an account may have, as the API takes them. */
const MAX_IDENTITIES = 50

type Field =
  | 'name'
  | 'colour'
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
    case 'name':
    case 'colour':
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
  name: string
  colour: AccountColour | null
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
    // The name as stored: an unnamed account's field stays empty, whatever it is called meanwhile.
    name: account?.name ?? '',
    colour: account?.colour ?? null,
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

/** The form with `suggestion`'s servers and login for `address`. */
function withSuggestion(state: FormState, suggestion: Suggestion, address: string): FormState {
  return {
    ...state,
    login: suggestion.login === 'address' ? address : address.slice(0, address.lastIndexOf('@')),
    imapHost: suggestion.imap.host,
    imapPort: String(suggestion.imap.port),
    imapTls: suggestion.imap.tls,
    smtpHost: suggestion.smtp.host,
    smtpPort: String(suggestion.smtp.port),
    smtpTls: suggestion.smtp.tls,
  }
}

/** The hosts of `suggestion` outside the address's domain, each once. */
function hostsOutside(suggestion: Suggestion): string[] {
  const hosts = [suggestion.imap, suggestion.smtp]
    .filter((endpoint) => endpoint.outside_domain)
    .map((endpoint) => endpoint.host)
  return [...new Set(hosts)]
}

/**
 * Find settings: what the lookup of the address answered. `confirm` holds a suggestion naming a
 * host outside the domain until the user says whether to use it; `declined` is one they did not.
 */
type Lookup =
  | { kind: 'idle' }
  | { kind: 'looking' }
  | { kind: 'found'; suggestion: Suggestion }
  | { kind: 'confirm'; suggestion: Suggestion; domain: string; address: string }
  | { kind: 'declined' }
  | { kind: 'none'; domain: string }
  | { kind: 'failed'; message: string }

/** The lookup's outcome worded for the reader: where the settings came from, or why there are none. */
function lookupMessage(t: TFunction, lookup: Lookup): string | null {
  switch (lookup.kind) {
    case 'idle':
      return null
    case 'looking':
      return t('accounts.autoconfig.looking')
    case 'none':
      return t('accounts.autoconfig.none', { domain: lookup.domain })
    case 'failed':
      return lookup.message
    case 'declined':
      return t('accounts.autoconfig.declined')
    case 'confirm': {
      const { suggestion, domain } = lookup
      const hosts = hostsOutside(suggestion)
      return [
        t(`accounts.autoconfig.sources.${suggestion.source}`, {
          origin: suggestion.origin ?? '',
          provider: suggestion.provider ?? '',
        }),
        t('accounts.autoconfig.outsideDomain', { count: hosts.length, domain, hosts: hosts.join(', ') }),
      ].join(' ')
    }
    case 'found': {
      const { suggestion } = lookup
      const provider = suggestion.provider ?? ''
      if (!suggestion.supported) {
        return t('accounts.autoconfig.unsupported', { provider })
      }
      const parts = [
        t(`accounts.autoconfig.sources.${suggestion.source}`, { origin: suggestion.origin ?? '', provider }),
        suggestion.note === 'app_password' ? t('accounts.autoconfig.appPassword', { provider }) : null,
        t('accounts.autoconfig.check'),
      ]
      return parts.filter((part) => part !== null).join(' ')
    }
  }
}

function lookupFailure(t: TFunction, failure: unknown): string {
  if (failure instanceof ApiRequestError) {
    switch (failure.code) {
      case 'invalid_address':
        return t('accounts.autoconfig.invalidAddress')
      case 'autoconfig_busy':
        return t('accounts.autoconfig.busy')
    }
  }
  return t('accounts.autoconfig.failed')
}

function fieldsOf(state: FormState, account: Account | undefined): AccountFields {
  const many = state.identities.length > 1
  const identities = state.identities.map((identity, index) => {
    const identityName = identity.name.trim()
    return {
      address: identity.address.trim(),
      name: identityName === '' ? null : identityName,
      ...(many ? { default: index === state.defaultIdentity } : {}),
    }
  })
  const name = state.name.trim()
  return {
    name: name === '' ? null : name,
    colour: state.colour,
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
 * Adds an account (no `account`), which the server numbers, or edits one, where an empty password
 * keeps the stored one. Adding starts from the email address: Find settings fills the servers and
 * login from the API's suggestion and says where it came from, first asking whether to use a
 * suggestion naming a host outside the address's domain; when nothing is found, or the user does
 * not use it, the presets are offered; entering the settings by hand is always possible. The account's name and colour are
 * how the web shows it.
 * Every identity is edited here: the first, more added or removed, and which one is
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
  save: (fields: AccountFields) => Promise<unknown>
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
  const [step, setStep] = useState<'address' | 'details'>(editing ? 'details' : 'address')
  const [lookup, setLookup] = useState<Lookup>({ kind: 'idle' })
  const lookingUp = useRef<AbortController | null>(null)
  const loginInput = useRef<HTMLInputElement>(null)
  const passwordInput = useRef<HTMLInputElement>(null)
  const focusNext = useRef<'login' | 'password' | null>(null)

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

  useEffect(() => () => lookingUp.current?.abort(), [])

  useEffect(() => {
    const next = focusNext.current
    focusNext.current = null
    if (next !== null) {
      ;(next === 'login' ? loginInput : passwordInput).current?.focus()
    }
  }, [step, lookup])

  /**
   * Fills the servers and the login for `address` from `suggestion`, unless Mailroom cannot sign in
   * to its provider.
   */
  function apply(suggestion: Suggestion, address: string): void {
    setLookup({ kind: 'found', suggestion })
    if (suggestion.supported) {
      setState((current) => withSuggestion(current, suggestion, address))
      setStep('details')
    }
  }

  /** Stops a running lookup, and forgets what the last one said. */
  function forgetLookup(): void {
    lookingUp.current?.abort()
    setLookup((current) => (current.kind === 'idle' ? current : { kind: 'idle' }))
  }

  async function find(): Promise<void> {
    const address = state.identities[0]?.address.trim() ?? ''
    if (!address.includes('@')) {
      setLookup({ kind: 'failed', message: t('accounts.autoconfig.invalidAddress') })
      return
    }
    lookingUp.current?.abort()
    const controller = new AbortController()
    lookingUp.current = controller
    setLookup({ kind: 'looking' })
    try {
      const answer = await findSettings(address, controller.signal)
      if (controller.signal.aborted) {
        return
      }
      if (answer.suggestion === null) {
        setLookup({ kind: 'none', domain: answer.domain })
      } else if (answer.suggestion.supported && hostsOutside(answer.suggestion).length > 0) {
        // A host the domain names outside itself would get the password: the user decides first.
        setLookup({ kind: 'confirm', suggestion: answer.suggestion, domain: answer.domain, address })
      } else {
        apply(answer.suggestion, address)
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        setLookup({ kind: 'failed', message: lookupFailure(t, failure) })
      }
    }
  }

  function enterManually(): void {
    if (lookup.kind === 'looking') {
      forgetLookup()
    }
    focusNext.current = 'login'
    setStep('details')
  }

  useEffect(() => {
    if (focusAdd.current) {
      focusAdd.current = false
      addButton.current?.focus()
    }
  }, [state.identities])

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (step === 'address') {
      await find()
      return
    }
    setSubmitting(true)
    setError(null)
    setIssues({})
    try {
      await save(fieldsOf(state, account))
    } catch (failure) {
      const byField: Partial<Record<Field, string>> = {}
      const general: string[] = []
      if (failure instanceof ApiRequestError) {
        // Another account has the name: said beside the name field, in the reader's language.
        const nameTaken = failure.code === 'account_name_taken'
        for (const issue of refusalIssues(failure.body)) {
          const field = fieldOf(issue, state.identities.length)
          if (field === null) {
            general.push(issue.message)
          } else {
            const message = nameTaken && field === 'name' ? t('accountErrors.accountNameTaken') : issue.message
            byField[field] = byField[field] === undefined ? message : `${byField[field]} ${message}`
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

  const firstAddress = identityField(0, 'address')
  const lookupId = `${prefix}-lookup`
  const message = lookupMessage(t, lookup)
  const offerPresets = lookup.kind === 'none' || lookup.kind === 'failed' || lookup.kind === 'declined'
  /** Adding: the first address, with Find settings, what it found, the presets and manual entry. */
  const addressRow = (
    <div className="flex flex-col gap-2">
      {row(
        firstAddress,
        t('accounts.address'),
        <div className="flex flex-wrap gap-2">
          <Input
            {...control(firstAddress, true)}
            type="email"
            required
            maxLength={320}
            className="min-w-48 flex-1"
            value={state.identities[0]?.address ?? ''}
            onChange={(event) => {
              forgetLookup()
              setIdentity(0, 'address', event.target.value)
            }}
          />
          <Button
            type="button"
            variant="outline"
            aria-describedby={message === null ? undefined : lookupId}
            disabled={lookup.kind === 'looking'}
            onClick={() => void find()}
          >
            {t('accounts.autoconfig.find')}
          </Button>
        </div>,
        t('accounts.autoconfig.hint'),
      )}
      <p id={lookupId} role="status" className="text-sm">
        {message}
      </p>
      {lookup.kind === 'confirm' && (
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            aria-describedby={lookupId}
            onClick={() => {
              focusNext.current = 'password'
              apply(lookup.suggestion, lookup.address)
            }}
          >
            {t('accounts.autoconfig.useOutside')}
          </Button>
          <Button
            type="button"
            variant="outline"
            aria-describedby={lookupId}
            onClick={() => {
              setLookup({ kind: 'declined' })
            }}
          >
            {t('accounts.autoconfig.declineOutside')}
          </Button>
        </div>
      )}
      {offerPresets && (
        <div role="group" aria-label={t('accounts.autoconfig.presets')} className="flex flex-wrap gap-2">
          {PRESETS.map((preset) => (
            <Button
              key={preset.provider}
              type="button"
              variant="outline"
              onClick={() => {
                focusNext.current = 'password'
                apply(preset, state.identities[0]?.address.trim() ?? '')
              }}
            >
              {t('accounts.autoconfig.usePreset', { provider: preset.provider })}
            </Button>
          ))}
        </div>
      )}
      {step === 'address' && (
        <Button type="button" variant="ghost" className="self-start" onClick={enterManually}>
          {t('accounts.autoconfig.manual')}
        </Button>
      )}
    </div>
  )

  return (
    <form
      aria-labelledby={titleId}
      aria-describedby={error === null ? undefined : errorId}
      className="flex flex-col gap-3 rounded-lg border p-4"
      onSubmit={(event) => void submit(event)}
    >
      <h2 id={titleId} className="font-medium">
        {editing ? t('accounts.editTitle', { account: account.display_name }) : t('accounts.add')}
      </h2>
      {!editing && addressRow}
      {step === 'details' && (
        <>
          {row(
            'name',
            t('accounts.displayName'),
            <Input
              {...control('name', true)}
              maxLength={64}
              placeholder={state.login}
              value={state.name}
              onChange={(event) => {
                set('name', event.target.value)
              }}
            />,
            t('accounts.displayNameHint'),
          )}
          <fieldset aria-describedby={issues.colour === undefined ? undefined : `${prefix}-colour-issue`} className="flex flex-col gap-1">
            <legend className="mb-1 text-sm font-medium">{t('accounts.colour')}</legend>
            <div className="flex flex-wrap gap-2">
              {[null, ...ACCOUNT_COLOURS].map((colour) => {
                const label = t(`accounts.colours.${colour ?? 'none'}`)
                return (
                  <label
                    key={colour ?? 'none'}
                    title={label}
                    className="flex size-8 cursor-pointer items-center justify-center rounded-full border has-checked:border-foreground has-checked:ring-2 has-checked:ring-ring has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-ring"
                  >
                    <input
                      type="radio"
                      name={`${prefix}-colour`}
                      className="sr-only"
                      checked={state.colour === colour}
                      onChange={() => {
                        set('colour', colour)
                      }}
                    />
                    <span className="sr-only">{label}</span>
                    {colour === null ? (
                      <Ban aria-hidden="true" className="size-4 text-muted-foreground" />
                    ) : (
                      <AccountDot colour={colour} className="size-4" />
                    )}
                  </label>
                )
              })}
            </div>
            {issues.colour !== undefined && (
              <p id={`${prefix}-colour-issue`} className="text-sm text-destructive">
                {issues.colour}
              </p>
            )}
          </fieldset>
          {state.identities.map((identity, index) => {
            const address = identityField(index, 'address')
            const name = identityField(index, 'name')
            const first = index === 0
            const fields = (
              <>
                {(!first || editing) &&
                  row(
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
              ref={loginInput}
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
              ref={passwordInput}
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
        </>
      )}
      <div className="flex gap-2">
        {step === 'details' && (
          <Button type="submit" disabled={submitting}>
            {editing ? t('accounts.save') : t('accounts.addButton')}
          </Button>
        )}
        <Button type="button" variant="ghost" onClick={onCancel}>
          {t('accounts.cancel')}
        </Button>
      </div>
    </form>
  )
}

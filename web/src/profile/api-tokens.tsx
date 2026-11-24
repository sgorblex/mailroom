import { useCallback, useEffect, useId, useRef, useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'

import {
  createApiToken,
  EXPIRIES,
  listApiTokens,
  revokeApiToken,
  type ApiToken,
  type ApiTokens as Listing,
  type CreatedApiToken,
  type Expiry,
} from '@/api/api-tokens.ts'
import { apiErrorMessage, ApiRequestError } from '@/api/http.ts'
import { useTimeZone } from '@/auth/auth-context.ts'
import { ConfirmRemoval } from '@/components/confirm-removal.tsx'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'
import { formatDateTime } from '@/lib/format.ts'
import { useLoad } from '@/lib/use-load.ts'
import { groupLabel } from '@/oauth/group-choice.ts'
import { GroupChoices } from '@/oauth/group-choices.tsx'
import { anySends, ceilingShown, limitsApprovable, NO_LIMITS } from '@/oauth/limit-choice.ts'
import { LimitChoices } from '@/oauth/limit-choices.tsx'

/**
 * The user's personal API tokens, for scripts and command-line clients: made here with the tools,
 * accounts and send ceiling chosen, shown once, listed and revoked after a confirm. Nothing on an
 * instance that has nothing to give one, once there are none.
 */
export function ApiTokens(): ReactElement | null {
  const { t, i18n } = useTranslation()
  const timeZone = useTimeZone()
  const [changes, setChanges] = useState(0)
  const [failure, setFailure] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [confirming, setConfirming] = useState<number | null>(null)
  const [making, setMaking] = useState(false)
  // The token just made: the only time its text is known, dropped once the user is done with it.
  const [created, setCreated] = useState<CreatedApiToken | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const makeButton = useRef<HTMLButtonElement>(null)
  // Each row's Revoke button, to give focus back to when its confirm is cancelled.
  const triggers = useRef(new Map<number, HTMLButtonElement>())
  const load = useCallback((signal: AbortSignal) => listApiTokens(signal), [])
  const { state, retry } = useLoad(
    load,
    (error) => apiErrorMessage(error, t('apiTokens.loadFailed')),
    changes,
  )

  async function revoke(token: ApiToken): Promise<void> {
    setPending(true)
    setFailure(null)
    try {
      await revokeApiToken(token.id)
      if (created?.id === token.id) {
        setCreated(null)
      }
    } catch (error) {
      // Gone already is what was asked for: the list shows it.
      if (!(error instanceof ApiRequestError && error.status === 404)) {
        setFailure(apiErrorMessage(error, t('apiTokens.revokeFailed')))
      }
    } finally {
      // The confirm goes, and with it the focus.
      heading.current?.focus()
      setPending(false)
      setConfirming(null)
      setChanges((value) => value + 1)
    }
  }

  function details(token: ApiToken): string {
    const at = (iso: string): string => formatDateTime(i18n.language, iso, timeZone)
    return [
      `${token.prefix}…`,
      t('apiTokens.created', { when: at(token.created_at) }),
      token.last_used_at === null
        ? t('apiTokens.neverUsed')
        : t('apiTokens.lastUsed', { when: at(token.last_used_at) }),
      token.expires_at === null
        ? t('apiTokens.neverExpires')
        : token.expired
          ? t('apiTokens.expired', { when: at(token.expires_at) })
          : t('apiTokens.expires', { when: at(token.expires_at) }),
    ].join(' · ')
  }

  const listing = state.kind === 'loaded' ? state.value : null
  const tokens = listing?.tokens ?? []
  if (listing !== null && listing.groups.length === 0 && tokens.length === 0) {
    return null
  }
  return (
    <section aria-labelledby="api-tokens" className="flex flex-col gap-3">
      <h3 id="api-tokens" ref={heading} tabIndex={-1} className="font-medium outline-none">
        {t('apiTokens.title')}
      </h3>
      <p className="text-sm text-muted-foreground">{t('apiTokens.intro')}</p>
      {state.kind === 'loading' && (
        <p className="text-sm text-muted-foreground">{t('apiTokens.loading')}</p>
      )}
      {state.kind === 'failed' && (
        <div className="flex flex-col items-start gap-3">
          <p role="alert" className="text-sm text-destructive">
            {state.message}
          </p>
          <Button variant="outline" size="sm" onClick={retry}>
            {t('apiTokens.retry')}
          </Button>
        </div>
      )}
      {created !== null && (
        <NewTokenText
          token={created}
          onDone={() => {
            setCreated(null)
            heading.current?.focus()
          }}
        />
      )}
      {tokens.length > 0 && (
        <ul aria-label={t('apiTokens.title')} className="flex flex-col divide-y rounded-lg border">
          {tokens.map((token) => (
            <li key={token.id} className="flex flex-col gap-2 p-3">
              <div className="flex items-center gap-3">
                <div className="mr-auto min-w-0">
                  <p className="text-sm font-medium break-words">{token.name}</p>
                  <p className="text-xs break-words text-muted-foreground">{details(token)}</p>
                </div>
                {confirming !== token.id && (
                  <Button
                    ref={(button) => {
                      if (button === null) {
                        triggers.current.delete(token.id)
                      } else {
                        triggers.current.set(token.id, button)
                      }
                    }}
                    variant="outline"
                    size="sm"
                    aria-label={t('apiTokens.revokeNamed', { name: token.name })}
                    disabled={pending}
                    onClick={() => {
                      setConfirming(token.id)
                      setFailure(null)
                    }}
                  >
                    {t('apiTokens.revoke')}
                  </Button>
                )}
              </div>
              <TokenScope token={token} accounts={listing?.accounts ?? []} />
              {confirming === token.id && (
                <ConfirmRemoval
                  question={t('apiTokens.confirmRevoke', { name: token.name })}
                  confirmLabel={t('apiTokens.revoke')}
                  cancelLabel={t('apiTokens.cancel')}
                  pending={pending}
                  onConfirm={() => void revoke(token)}
                  onCancel={() => {
                    setConfirming(null)
                    requestAnimationFrame(() => triggers.current.get(token.id)?.focus())
                  }}
                />
              )}
            </li>
          ))}
        </ul>
      )}
      {failure !== null && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
      {listing !== null &&
        listing.groups.length > 0 &&
        (making ? (
          <NewTokenForm
            listing={listing}
            onCreated={(token) => {
              setCreated(token)
              setMaking(false)
              setChanges((value) => value + 1)
            }}
            onCancel={() => {
              setMaking(false)
              requestAnimationFrame(() => makeButton.current?.focus())
            }}
          />
        ) : (
          <Button
            ref={makeButton}
            className="self-start"
            variant="outline"
            size="sm"
            onClick={() => {
              setMaking(true)
              setCreated(null)
            }}
          >
            {t('apiTokens.new')}
          </Button>
        ))}
    </section>
  )
}

/** What a token may do, in words. */
function TokenScope({
  token,
  accounts,
}: {
  token: ApiToken
  accounts: Listing['accounts']
}): ReactElement {
  const { t } = useTranslation()
  const ids = token.account_ids
  const names = accounts.filter((account) => ids?.includes(account.id)).map(({ name }) => name)
  const ceiling = ceilingShown(token.send_ceiling)
  return (
    <div className="flex flex-col gap-1 text-xs break-words">
      <p>
        {token.groups.length === 0
          ? t('apiTokens.noGroups')
          : t('connectedApps.groups', {
              groups: token.groups.map((name) => groupLabel(t, name)).join(', '),
            })}
      </p>
      <p>
        {ids === null
          ? t('connectedApps.everyAccount')
          : names.length === 0
            ? t('connectedApps.noAccounts')
            : t('connectedApps.accounts', { accounts: names.join(', ') })}
      </p>
      {ceiling !== null && (
        <p>{t('connectedApps.ceiling', { mode: t(`accounts.sendModes.${ceiling}`) })}</p>
      )}
    </div>
  )
}

/** The token just made, to copy: it is not shown again. */
function NewTokenText({
  token,
  onDone,
}: {
  token: CreatedApiToken
  onDone: () => void
}): ReactElement {
  const { t } = useTranslation()
  const id = useId()
  const field = useRef<HTMLInputElement>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    field.current?.focus()
  }, [])

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(token.token)
      setNotice(t('apiTokens.copied'))
    } catch {
      // No clipboard (plain HTTP, or refused): selected, for the user to copy.
      field.current?.focus()
      field.current?.select()
      setNotice(t('apiTokens.copyFailed'))
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-md border p-3">
      <label htmlFor={id} className="text-sm font-medium">
        {t('apiTokens.yourToken', { name: token.name })}
      </label>
      <Input
        id={id}
        ref={field}
        readOnly
        autoComplete="off"
        spellCheck={false}
        className="font-mono"
        aria-describedby={`${id}-once`}
        value={token.token}
        onFocus={(event) => {
          event.target.select()
        }}
      />
      <p id={`${id}-once`} className="text-xs text-muted-foreground">
        {t('apiTokens.shownOnce')}
      </p>
      <div className="flex gap-2">
        <Button size="sm" onClick={() => void copy()}>
          {t('apiTokens.copy')}
        </Button>
        <Button size="sm" variant="outline" onClick={onDone}>
          {t('apiTokens.done')}
        </Button>
      </div>
      <p role="status" className="text-sm empty:hidden">
        {notice}
      </p>
    </div>
  )
}

const NEVER = 'never'

function NewTokenForm({
  listing,
  onCreated,
  onCancel,
}: {
  listing: Listing
  onCreated: (token: CreatedApiToken) => void
  onCancel: () => void
}): ReactElement {
  const { t } = useTranslation()
  const id = useId()
  const [name, setName] = useState('')
  const [groups, setGroups] = useState<string[]>([])
  const [limits, setLimits] = useState(NO_LIMITS)
  const [expiry, setExpiry] = useState<Expiry | null>(90)
  const [pending, setPending] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const nameField = useRef<HTMLInputElement>(null)

  useEffect(() => {
    nameField.current?.focus()
  }, [])

  const ready = name.trim() !== '' && groups.length > 0 && limitsApprovable(limits)

  async function create(): Promise<void> {
    setPending(true)
    setFailure(null)
    try {
      const ceiling = anySends(groups) ? limits.send_ceiling : null
      onCreated(
        await createApiToken({
          name: name.trim(),
          groups,
          account_ids: limits.account_ids,
          send_ceiling: ceiling,
          expires_in_days: expiry,
        }),
      )
    } catch (error) {
      setFailure(
        error instanceof ApiRequestError && error.code === 'too_many_tokens'
          ? t('apiTokens.tooMany')
          : apiErrorMessage(error, t('apiTokens.createFailed')),
      )
      setPending(false)
    }
  }

  return (
    <form
      aria-label={t('apiTokens.new')}
      className="flex flex-col gap-3 rounded-md border p-3"
      onSubmit={(event: SubmitEvent) => {
        event.preventDefault()
        if (ready && !pending) {
          void create()
        }
      }}
    >
      <div className="flex flex-col gap-1">
        <label htmlFor={`${id}-name`} className="text-sm font-medium">
          {t('apiTokens.name')}
        </label>
        <Input
          id={`${id}-name`}
          ref={nameField}
          autoComplete="off"
          maxLength={100}
          placeholder={t('apiTokens.namePlaceholder')}
          disabled={pending}
          value={name}
          onChange={(event) => {
            setName(event.target.value)
          }}
        />
      </div>
      <GroupChoices
        groups={listing.groups}
        chosen={groups}
        onChange={setGroups}
        disabled={pending}
      />
      <LimitChoices
        accounts={listing.accounts}
        limits={limits}
        onChange={setLimits}
        sends={anySends(groups)}
        disabled={pending}
      />
      <div className="flex flex-col gap-1 text-sm">
        <label htmlFor={`${id}-expiry`} className="font-medium">
          {t('apiTokens.expiry')}
        </label>
        <select
          id={`${id}-expiry`}
          className="h-9 rounded-md border bg-transparent px-2"
          disabled={pending}
          value={expiry === null ? NEVER : String(expiry)}
          onChange={(event) => {
            setExpiry(EXPIRIES.find((days) => String(days) === event.target.value) ?? null)
          }}
        >
          {EXPIRIES.map((days) => (
            <option key={days} value={days}>
              {t('apiTokens.days', { count: days })}
            </option>
          ))}
          <option value={NEVER}>{t('apiTokens.never')}</option>
        </select>
      </div>
      {failure !== null && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={pending || !ready}>
          {t('apiTokens.create')}
        </Button>
        <Button type="button" size="sm" variant="outline" disabled={pending} onClick={onCancel}>
          {t('apiTokens.cancel')}
        </Button>
      </div>
    </form>
  )
}

import { useCallback, useState, type ReactElement, type SubmitEvent } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { ApiRequestError, apiErrorMessage } from '@/api/http.ts'
import {
  changeInstanceSettings,
  getInstanceSettings,
  MAX_RETENTION_DAYS,
  SESSION_IP_CHOICES,
  type InstanceSettings,
  type InstanceSettingsAnswer,
} from '@/api/instance-settings.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'
import { useLoad } from '@/lib/use-load.ts'

type Outcome = { kind: 'idle' } | { kind: 'saved' } | { kind: 'failed'; message: string }

const SESSION_IP_LABELS = {
  none: 'instance.sessionIpNone',
  partial: 'instance.sessionIpPartial',
  full: 'instance.sessionIpFull',
} as const

/** What an admin chooses for the whole instance; `onSaved` hears of a change. */
export function InstanceSettingsForm({ onSaved }: { onSaved?: () => void }): ReactElement {
  const { t } = useTranslation()
  const load = useCallback((signal: AbortSignal) => getInstanceSettings(signal), [])
  const { state, retry } = useLoad(load, (error) => apiErrorMessage(error, t('instance.loadFailed')))
  return (
    <section aria-labelledby="instance-title" className="flex flex-col gap-3 rounded-lg border p-4">
      <h2 id="instance-title" className="font-medium">
        {t('instance.title')}
      </h2>
      <p className="text-sm text-muted-foreground">{t('instance.intro')}</p>
      {state.kind === 'loading' && <p className="text-sm">{t('instance.loading')}</p>}
      {state.kind === 'failed' && (
        <div className="flex flex-col items-start gap-3">
          <p role="alert" className="text-sm text-destructive">
            {state.message}
          </p>
          <Button type="button" variant="outline" size="sm" onClick={retry}>
            {t('instance.retry')}
          </Button>
        </div>
      )}
      {state.kind === 'loaded' && <InstanceSettingsFields initial={state.value} onSaved={onSaved} />}
    </section>
  )
}

/** The settings of `wanted` that are not as in `loaded`. */
function changesFrom(loaded: InstanceSettings, wanted: InstanceSettings): Partial<InstanceSettings> {
  const changes: Partial<InstanceSettings> = {}
  if (wanted.audit_retention_days !== loaded.audit_retention_days) {
    changes.audit_retention_days = wanted.audit_retention_days
  }
  if (wanted.session_ip !== loaded.session_ip) {
    changes.session_ip = wanted.session_ip
  }
  if (wanted.external_sign_in_only !== loaded.external_sign_in_only) {
    changes.external_sign_in_only = wanted.external_sign_in_only
  }
  return changes
}

const REFUSALS = {
  external_sign_in_not_configured: 'instance.externalNotConfigured',
  external_sign_in_not_linked: 'instance.externalNotLinked',
} as const

function failureMessage(t: TFunction, failure: unknown): string {
  const code = failure instanceof ApiRequestError ? failure.code : undefined
  if (code === 'external_sign_in_not_configured' || code === 'external_sign_in_not_linked') {
    return t(REFUSALS[code])
  }
  return apiErrorMessage(failure, t('instance.saveFailed'))
}

function InstanceSettingsFields({
  initial,
  onSaved,
}: {
  initial: InstanceSettingsAnswer
  onSaved: (() => void) | undefined
}): ReactElement {
  const { t } = useTranslation()
  const [days, setDays] = useState(String(initial.audit_retention_days))
  const [sessionIp, setSessionIp] = useState(initial.session_ip)
  const [externalOnly, setExternalOnly] = useState(initial.external_sign_in_only)
  const [stored, setStored] = useState(initial)
  const [saving, setSaving] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' })

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setSaving(true)
    setOutcome({ kind: 'idle' })
    try {
      const saved = await changeInstanceSettings(
        changesFrom(stored, {
          audit_retention_days: Number(days),
          session_ip: sessionIp,
          external_sign_in_only: externalOnly,
        }),
      )
      setDays(String(saved.audit_retention_days))
      setSessionIp(saved.session_ip)
      setExternalOnly(saved.external_sign_in_only)
      setStored(saved)
      setOutcome({ kind: 'saved' })
      onSaved?.()
    } catch (failure) {
      setOutcome({ kind: 'failed', message: failureMessage(t, failure) })
    } finally {
      setSaving(false)
    }
  }

  const externalNote =
    stored.external_sign_in === 'not_configured'
      ? t('instance.externalNoProvider')
      : stored.password_sign_in_forced
        ? t('instance.externalOverridden')
        : stored.external_sign_in === 'not_linked'
          ? t('instance.externalNotLinked')
          : null

  return (
    <form className="flex flex-col gap-3" onSubmit={(event) => void submit(event)}>
      <label htmlFor="instance-retention" className="text-sm font-medium">
        {t('instance.retention')}
      </label>
      <Input
        id="instance-retention"
        type="number"
        inputMode="numeric"
        required
        min={1}
        max={MAX_RETENTION_DAYS}
        step={1}
        value={days}
        aria-describedby="instance-retention-hint"
        onChange={(event) => {
          setDays(event.target.value)
          setOutcome({ kind: 'idle' })
        }}
      />
      <p id="instance-retention-hint" className="text-sm text-muted-foreground">
        {t('instance.retentionHint', { max: MAX_RETENTION_DAYS })}
      </p>
      <label htmlFor="instance-session-ip" className="text-sm font-medium">
        {t('instance.sessionIp')}
      </label>
      <select
        id="instance-session-ip"
        className="h-9 min-w-0 rounded-md border bg-background px-2 text-sm text-foreground"
        value={sessionIp}
        aria-describedby="instance-session-ip-hint"
        onChange={(event) => {
          const choice = SESSION_IP_CHOICES.find((value) => value === event.target.value)
          if (choice !== undefined) {
            setSessionIp(choice)
            setOutcome({ kind: 'idle' })
          }
        }}
      >
        {SESSION_IP_CHOICES.map((choice) => (
          <option key={choice} value={choice}>
            {t(SESSION_IP_LABELS[choice])}
          </option>
        ))}
      </select>
      <p id="instance-session-ip-hint" className="text-sm text-muted-foreground">
        {t('instance.sessionIpHint')}
      </p>
      {/* Without a provider there is nothing to offer, but what is on can always be turned off. */}
      {(stored.external_sign_in !== 'not_configured' || stored.external_sign_in_only) && (
        <>
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={externalOnly}
              disabled={stored.external_sign_in !== 'available' && !stored.external_sign_in_only}
              aria-describedby="instance-external-hint"
              onChange={(event) => {
                setExternalOnly(event.target.checked)
                setOutcome({ kind: 'idle' })
              }}
            />
            {t('instance.externalOnly')}
          </label>
          <div
            id="instance-external-hint"
            className="flex flex-col gap-2 text-sm text-muted-foreground"
          >
            <p>{t('instance.externalOnlyHint')}</p>
            {externalNote !== null && <p>{externalNote}</p>}
          </div>
        </>
      )}
      {outcome.kind === 'failed' && (
        <p role="alert" className="text-sm text-destructive">
          {outcome.message}
        </p>
      )}
      {outcome.kind === 'saved' && (
        <p role="status" className="text-sm">
          {t('instance.saved')}
        </p>
      )}
      <Button type="submit" className="self-start" disabled={saving}>
        {t('instance.save')}
      </Button>
    </form>
  )
}

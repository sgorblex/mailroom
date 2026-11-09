import { useCallback, useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiRequestError, apiErrorMessage, refusalIssues } from '@/api/http.ts'
import { ATTACHMENT_MAX_MB, changeLimits, getLimits, type Limits } from '@/api/limits.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'
import { useLoad } from '@/lib/use-load.ts'

type Key = keyof Limits

/** Each limit with the bounds the browser checks; the API checks the rest (above 0 MB). */
const FIELDS: readonly { key: Key; min?: number; max?: number; step: number | 'any' }[] = [
  { key: 'send_per_hour', min: 1, step: 1 },
  { key: 'max_recipients', min: 1, step: 1 },
  { key: 'attachment_max_mb', max: ATTACHMENT_MAX_MB, step: 'any' },
  { key: 'body_chars', min: 500, step: 1 },
]

type Draft = Record<Key, string>

type Outcome = { kind: 'idle' } | { kind: 'saved' } | { kind: 'failed'; message: string; fields: Key[] }

function draftOf(limits: Limits): Draft {
  return {
    send_per_hour: String(limits.send_per_hour),
    max_recipients: String(limits.max_recipients),
    body_chars: String(limits.body_chars),
    attachment_max_mb: String(limits.attachment_max_mb),
  }
}

/** The user's mail limits, for the web and MCP alike; only changed here, never by MCP. */
export function LimitsForm(): ReactElement {
  const { t } = useTranslation()
  const load = useCallback((signal: AbortSignal) => getLimits(signal), [])
  const { state, retry } = useLoad(load, (error) => apiErrorMessage(error, t('limits.loadFailed')))
  return (
    <section aria-labelledby="limits" className="flex flex-col gap-3">
      <h2 id="limits" className="font-medium">
        {t('limits.title')}
      </h2>
      <p className="text-sm text-muted-foreground">{t('limits.intro')}</p>
      {state.kind === 'loading' && <p className="text-sm">{t('limits.loading')}</p>}
      {state.kind === 'failed' && (
        <div className="flex flex-col gap-2">
          <p role="alert" className="text-sm text-destructive">
            {state.message}
          </p>
          <Button type="button" variant="outline" onClick={retry}>
            {t('limits.retry')}
          </Button>
        </div>
      )}
      {state.kind === 'loaded' && <LimitsFields initial={state.value} />}
    </section>
  )
}

function LimitsFields({ initial }: { initial: Limits }): ReactElement {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<Draft>(() => draftOf(initial))
  const [saving, setSaving] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' })

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setSaving(true)
    setOutcome({ kind: 'idle' })
    try {
      const saved = await changeLimits({
        send_per_hour: Number(draft.send_per_hour),
        max_recipients: Number(draft.max_recipients),
        body_chars: Number(draft.body_chars),
        attachment_max_mb: Number(draft.attachment_max_mb),
      })
      setDraft(draftOf(saved))
      setOutcome({ kind: 'saved' })
    } catch (failure) {
      const issues = failure instanceof ApiRequestError ? refusalIssues(failure.body) : []
      const fields = issues.map((issue) => issue.path[0]).filter((key): key is Key => typeof key === 'string' && key in draft)
      setOutcome({ kind: 'failed', message: apiErrorMessage(failure, t('limits.saveFailed')), fields })
    } finally {
      setSaving(false)
    }
  }

  const invalid = (key: Key): boolean => outcome.kind === 'failed' && outcome.fields.includes(key)
  return (
    <form className="flex flex-col gap-3" onSubmit={(event) => void submit(event)}>
      {FIELDS.map(({ key, min, max, step }) => (
        <div key={key} className="flex flex-col gap-1">
          <label htmlFor={`limits-${key}`} className="text-sm font-medium">
            {t(`limits.${key}`)}
          </label>
          <Input
            id={`limits-${key}`}
            type="number"
            inputMode={step === 1 ? 'numeric' : 'decimal'}
            required
            min={min}
            max={max}
            step={step}
            value={draft[key]}
            aria-invalid={invalid(key)}
            aria-describedby={invalid(key) ? `limits-${key}-hint limits-error` : `limits-${key}-hint`}
            onChange={(event) => {
              setDraft({ ...draft, [key]: event.target.value })
            }}
          />
          <p id={`limits-${key}-hint`} className="text-sm text-muted-foreground">
            {t(`limits.${key}Hint`, { max: ATTACHMENT_MAX_MB })}
          </p>
        </div>
      ))}
      {outcome.kind === 'failed' && (
        <p id="limits-error" role="alert" className="text-sm text-destructive">
          {outcome.message}
        </p>
      )}
      {outcome.kind === 'saved' && (
        <p role="status" className="text-sm">
          {t('limits.saved')}
        </p>
      )}
      <Button type="submit" disabled={saving}>
        {t('limits.save')}
      </Button>
    </form>
  )
}

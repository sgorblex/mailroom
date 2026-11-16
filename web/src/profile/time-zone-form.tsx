import { useMemo, useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { apiErrorMessage } from '@/api/http.ts'
import { useAuth } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { browserTimeZone } from '@/lib/time-zone.ts'

type Outcome = { kind: 'idle' } | { kind: 'saved' } | { kind: 'failed'; message: string }

/** The zones this browser knows, with UTC and `current` even where its list leaves them out. */
function zoneChoices(current: string): string[] {
  return [...new Set([...Intl.supportedValuesOf('timeZone'), 'UTC', current])].sort()
}

/** The zone the user's dates are written in, here and wherever Mailroom writes them. */
export function TimeZoneForm(): ReactElement {
  const { t } = useTranslation()
  const auth = useAuth()
  const saved = auth.status === 'signed-in' ? auth.user.time_zone : null
  const [zone, setZone] = useState(() => saved ?? browserTimeZone())
  const [saving, setSaving] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' })
  const choices = useMemo(() => zoneChoices(zone), [zone])

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setSaving(true)
    setOutcome({ kind: 'idle' })
    try {
      await auth.changeTimeZone(zone)
      setOutcome({ kind: 'saved' })
    } catch (failure) {
      setOutcome({ kind: 'failed', message: apiErrorMessage(failure, t('timeZone.saveFailed')) })
    } finally {
      setSaving(false)
    }
  }

  return (
    <section aria-labelledby="time-zone" className="flex flex-col gap-3">
      <h2 id="time-zone" className="font-medium">
        {t('timeZone.title')}
      </h2>
      <p id="time-zone-intro" className="text-sm text-muted-foreground">
        {t('timeZone.intro')}
      </p>
      <form className="flex flex-col gap-3" onSubmit={(event) => void submit(event)}>
        <label htmlFor="time-zone-choice" className="text-sm font-medium">
          {t('timeZone.label')}
        </label>
        <select
          id="time-zone-choice"
          className="h-9 min-w-0 rounded-md border bg-background px-2 text-sm text-foreground"
          value={zone}
          aria-describedby="time-zone-intro"
          onChange={(event) => {
            setZone(event.target.value)
            setOutcome({ kind: 'idle' })
          }}
        >
          {choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice.replaceAll('_', ' ')}
            </option>
          ))}
        </select>
        {outcome.kind === 'failed' && (
          <p role="alert" className="text-sm text-destructive">
            {outcome.message}
          </p>
        )}
        {outcome.kind === 'saved' && (
          <p role="status" className="text-sm">
            {t('timeZone.saved')}
          </p>
        )}
        <Button type="submit" disabled={saving}>
          {t('timeZone.save')}
        </Button>
      </form>
    </section>
  )
}

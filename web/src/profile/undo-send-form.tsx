import { useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { UNDO_SEND_SECONDS } from '@/api/auth.ts'
import { apiErrorMessage } from '@/api/http.ts'
import { useAuth } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'

type Outcome = { kind: 'idle' } | { kind: 'saved' } | { kind: 'failed'; message: string }

const CHOICES = [0, 5, 10, 15, 20, UNDO_SEND_SECONDS]

/** How long Send holds a message for an Undo; none sends at once. */
export function UndoSendForm(): ReactElement {
  const { t } = useTranslation()
  const auth = useAuth()
  const saved = auth.status === 'signed-in' ? auth.user.undo_send_seconds : 10
  const [seconds, setSeconds] = useState(saved)
  const [saving, setSaving] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' })
  const choices = CHOICES.includes(saved) ? CHOICES : [...CHOICES, saved].toSorted((a, b) => a - b)

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setSaving(true)
    setOutcome({ kind: 'idle' })
    try {
      await auth.changeUndoSend(seconds)
      setOutcome({ kind: 'saved' })
    } catch (failure) {
      setOutcome({ kind: 'failed', message: apiErrorMessage(failure, t('undoSend.saveFailed')) })
    } finally {
      setSaving(false)
    }
  }

  return (
    <section aria-labelledby="undo-send" className="flex flex-col gap-3">
      <h2 id="undo-send" className="font-medium">
        {t('undoSend.title')}
      </h2>
      <p id="undo-send-intro" className="text-sm text-muted-foreground">
        {t('undoSend.intro')}
      </p>
      <form className="flex flex-col gap-3" onSubmit={(event) => void submit(event)}>
        <label htmlFor="undo-send-choice" className="text-sm font-medium">
          {t('undoSend.label')}
        </label>
        <select
          id="undo-send-choice"
          className="h-9 min-w-0 rounded-md border bg-background px-2 text-sm text-foreground"
          value={seconds}
          aria-describedby="undo-send-intro"
          onChange={(event) => {
            setSeconds(Number(event.target.value))
            setOutcome({ kind: 'idle' })
          }}
        >
          {choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice === 0 ? t('undoSend.off') : t('undoSend.seconds', { count: choice })}
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
            {t('undoSend.saved')}
          </p>
        )}
        <Button type="submit" disabled={saving}>
          {t('undoSend.save')}
        </Button>
      </form>
    </section>
  )
}

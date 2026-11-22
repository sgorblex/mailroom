import { useId, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { useTimeZone } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { Dialog } from '@/components/ui/dialog.tsx'
import { Input } from '@/components/ui/input.tsx'
import { formatDateTime } from '@/lib/format.ts'

import { MAIL_NS } from './i18n.ts'
import { addDays, MAX_AHEAD_DAYS, presets, sendTimeProblem, wallTime, zonedInstant, type PresetKind } from './send-later.ts'

/** Send later: a quick choice, or a day and time of the user's own, in their time zone. */
export function SendLaterDialog({ onPick, onCancel }: { onPick: (at: Date) => void; onCancel: () => void }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  return (
    <LaterDialog
      title={t('compose.sendLater')}
      presetLabel={(kind) => t(`compose.later.${kind}`)}
      pickLabel={t('compose.laterSchedule')}
      tooFar={t('compose.laterTooFar')}
      onPick={onPick}
      onCancel={onCancel}
    />
  )
}

export interface LaterDialogProps {
  title: string
  /** A quick choice's name. */
  presetLabel: (kind: PresetKind) => string
  /** The button taking the date and time picked. */
  pickLabel: string
  /** Why a time over a year ahead is refused. */
  tooFar: string
  onPick: (at: Date) => void
  onCancel: () => void
}

/** A time to come, up to a year ahead: a quick choice, or a day and time of the user's own, in their time zone. */
export function LaterDialog({ title, presetLabel, pickLabel, tooFar, onPick, onCancel }: LaterDialogProps): ReactElement {
  const { t, i18n } = useTranslation(MAIL_NS)
  const problems = { invalid: t('compose.laterInvalid'), past: t('compose.laterPast'), tooFar }
  const timeZone = useTimeZone()
  const [now] = useState(() => new Date())
  const today = wallTime(now, timeZone)
  const [date, setDate] = useState(() => addDays(today.date, 1))
  const [time, setTime] = useState('08:00')
  const [problem, setProblem] = useState<string | null>(null)
  const dateId = useId()
  const timeId = useId()
  const problemId = useId()

  function pickOwn(): void {
    const at = zonedInstant(date, time, timeZone)
    const wrong = sendTimeProblem(at, new Date())
    if (at === null || wrong !== null) {
      setProblem(problems[wrong ?? 'invalid'])
      return
    }
    onPick(at)
  }

  return (
    <Dialog label={title} onCancel={onCancel}>
      <h2 className="text-base font-semibold">{title}</h2>
      <p className="mt-1 text-xs text-muted-foreground">{t('compose.laterZone', { zone: timeZone ?? Intl.DateTimeFormat().resolvedOptions().timeZone })}</p>
      <ul className="mt-3 flex flex-col gap-1">
        {presets(now, timeZone).map((preset) => (
          <li key={preset.kind}>
            <Button
              type="button"
              variant="ghost"
              className="w-full justify-between"
              onClick={() => {
                onPick(preset.at)
              }}
            >
              <span>{presetLabel(preset.kind)}</span>
              <span className="text-muted-foreground">{formatDateTime(i18n.language, preset.at.toISOString(), timeZone)}</span>
            </Button>
          </li>
        ))}
      </ul>
      <fieldset className="mt-3 flex flex-col gap-2 border-t pt-3">
        <legend className="sr-only">{t('compose.laterPick')}</legend>
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex flex-col gap-1">
            <label htmlFor={dateId} className="text-sm text-muted-foreground">
              {t('compose.laterDate')}
            </label>
            <Input
              id={dateId}
              type="date"
              min={today.date}
              max={addDays(today.date, MAX_AHEAD_DAYS)}
              value={date}
              aria-describedby={problem === null ? undefined : problemId}
              onChange={(event) => {
                setDate(event.target.value)
                setProblem(null)
              }}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor={timeId} className="text-sm text-muted-foreground">
              {t('compose.laterTime')}
            </label>
            <Input
              id={timeId}
              type="time"
              value={time}
              aria-describedby={problem === null ? undefined : problemId}
              onChange={(event) => {
                setTime(event.target.value)
                setProblem(null)
              }}
            />
          </div>
          <Button type="button" size="sm" onClick={pickOwn}>
            {pickLabel}
          </Button>
        </div>
        {problem !== null && (
          <p id={problemId} role="alert" className="text-sm text-destructive">
            {problem}
          </p>
        )}
      </fieldset>
      <div className="mt-4 flex justify-end">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          {t('compose.cancel')}
        </Button>
      </div>
    </Dialog>
  )
}

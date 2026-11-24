import { useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { apiErrorMessage } from '@/api/http.ts'
import { useAuth } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'

type Outcome = { kind: 'idle' } | { kind: 'saved' } | { kind: 'failed'; message: string }

/**
 * What "Mark all as read" on a folder with subfolders does first: the folder and its subfolders,
 * or the folder alone. The folder's menu offers the other one second.
 */
export function MarkReadForm(): ReactElement {
  const { t } = useTranslation()
  const auth = useAuth()
  const saved = auth.status === 'signed-in' ? auth.user.mark_read_subfolders : true
  const [subfolders, setSubfolders] = useState(saved)
  const [saving, setSaving] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' })

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setSaving(true)
    setOutcome({ kind: 'idle' })
    try {
      await auth.changeMarkReadSubfolders(subfolders)
      setOutcome({ kind: 'saved' })
    } catch (failure) {
      setOutcome({ kind: 'failed', message: apiErrorMessage(failure, t('markRead.saveFailed')) })
    } finally {
      setSaving(false)
    }
  }

  const choice = (value: boolean, label: string): ReactElement => (
    <label className="flex items-center gap-2 text-sm">
      <input
        type="radio"
        name="mark-read-subfolders"
        checked={subfolders === value}
        onChange={() => {
          setSubfolders(value)
          setOutcome({ kind: 'idle' })
        }}
      />
      {label}
    </label>
  )

  return (
    <section aria-labelledby="mark-read" className="flex flex-col gap-3">
      <h3 id="mark-read" className="font-medium">
        {t('markRead.title')}
      </h3>
      <form className="flex flex-col gap-3" onSubmit={(event) => void submit(event)}>
        <fieldset className="flex flex-col gap-2" aria-describedby="mark-read-intro">
          <legend className="sr-only">{t('markRead.title')}</legend>
          <p id="mark-read-intro" className="text-sm text-muted-foreground">
            {t('markRead.intro')}
          </p>
          {choice(true, t('markRead.subtree'))}
          {choice(false, t('markRead.folderOnly'))}
        </fieldset>
        {outcome.kind === 'failed' && (
          <p role="alert" className="text-sm text-destructive">
            {outcome.message}
          </p>
        )}
        {outcome.kind === 'saved' && (
          <p role="status" className="text-sm">
            {t('markRead.saved')}
          </p>
        )}
        <Button type="submit" disabled={saving}>
          {t('markRead.save')}
        </Button>
      </form>
    </section>
  )
}

import { useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { apiErrorMessage } from '@/api/http.ts'
import { useAuth } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'

type Outcome = { kind: 'idle' } | { kind: 'saved' } | { kind: 'failed'; message: string }

/** Whether the message lists show a conversation as one row. */
export function ConversationsForm(): ReactElement {
  const { t } = useTranslation()
  const auth = useAuth()
  const saved = auth.status === 'signed-in' ? auth.user.conversations : true
  const [grouped, setGrouped] = useState(saved)
  const [saving, setSaving] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' })

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setSaving(true)
    setOutcome({ kind: 'idle' })
    try {
      await auth.changeConversations(grouped)
      setOutcome({ kind: 'saved' })
    } catch (failure) {
      setOutcome({ kind: 'failed', message: apiErrorMessage(failure, t('conversations.saveFailed')) })
    } finally {
      setSaving(false)
    }
  }

  return (
    <section aria-labelledby="conversations" className="flex flex-col gap-3">
      <h3 id="conversations" className="font-medium">
        {t('conversations.title')}
      </h3>
      <form className="flex flex-col gap-3" onSubmit={(event) => void submit(event)}>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            name="conversations"
            className="mt-0.5"
            aria-describedby="conversations-intro"
            checked={grouped}
            onChange={(event) => {
              setGrouped(event.target.checked)
              setOutcome({ kind: 'idle' })
            }}
          />
          {t('conversations.group')}
        </label>
        <p id="conversations-intro" className="text-sm text-muted-foreground">
          {t('conversations.intro')}
        </p>
        {outcome.kind === 'failed' && (
          <p role="alert" className="text-sm text-destructive">
            {outcome.message}
          </p>
        )}
        {outcome.kind === 'saved' && (
          <p role="status" className="text-sm">
            {t('conversations.saved')}
          </p>
        )}
        <Button type="submit" disabled={saving}>
          {t('conversations.save')}
        </Button>
      </form>
    </section>
  )
}

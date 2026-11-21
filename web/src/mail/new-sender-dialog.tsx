import { useEffect, useRef, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button.tsx'
import { Dialog } from '@/components/ui/dialog.tsx'

import { newSenderIn } from './conversations.ts'
import { MAIL_NS } from './i18n.ts'
import { useTriage, type NewSender } from './triage-context.ts'

/** The question of an action on conversations whose newest sender is new to them, while one waits. */
export function NewSenderHost(): ReactElement | null {
  const { newSender } = useTriage()
  return newSender === null ? null : <NewSenderDialog asked={newSender} />
}

/**
 * Before a trash, archive, move or spam report takes such a conversation whole: how many messages
 * it has and that its newest sender is new to it, with the choice of the message shown alone.
 * Cancel (and Escape) is focused first: nothing happens unless the user picks.
 */
function NewSenderDialog({ asked }: { asked: NewSender }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { answerNewSender } = useTriage()
  const cancel = useRef<HTMLButtonElement>(null)
  const flagged = asked.targets.filter(newSenderIn)
  const [first] = flagged
  const one = flagged.length === 1 && first !== undefined
  const count = first?.conversation?.count ?? 0
  const title = t('conversation.newSender.title')
  useEffect(() => {
    cancel.current?.focus()
  }, [])
  return (
    <Dialog
      label={title}
      onCancel={() => {
        answerNewSender(null)
      }}
    >
      <div className="flex flex-col gap-3">
        <h2 className="text-base font-medium">{title}</h2>
        <p className="text-sm">
          {one ? t('conversation.newSender.one', { count }) : t('conversation.newSender.several', { count: flagged.length })}
        </p>
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button
            ref={cancel}
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              answerNewSender(null)
            }}
          >
            {t('triage.cancel')}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              answerNewSender('shown')
            }}
          >
            {one ? t('conversation.newSender.shown') : t('conversation.newSender.shownSeveral')}
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => {
              answerNewSender('whole')
            }}
          >
            {one ? t('conversation.newSender.whole', { count }) : t('conversation.newSender.wholeSeveral')}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'

import { Button } from '@/components/ui/button.tsx'

import { useCompose, type HeldNotice } from './compose-context.ts'
import { MAIL_NS } from './i18n.ts'
import { composeErrorMessage, heldEndMessage, mailCodeMessage, sentMessage } from './messages.ts'

/** Where the draft's send stands, while the composer is open. */
export type SendState =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'failed'; code: string | null }
  | { kind: 'refused'; error: unknown }
  /** Send found another version under the draft: the form is saved over it, and sent on the next press. */
  | { kind: 'relocated' }
  | { kind: 'unknown' }
  /** Delivered earlier, and this draft is what was left of it: reopened, or found out by a Send. */
  | { kind: 'sent' }

/** The send's state in the composer: the words, and the choices of a delivery that can't be confirmed. */
export function SendStatus({
  state,
  onOpenSent,
  onKeep,
  onSendAnyway,
}: {
  state: SendState
  /** `null` while the account's Sent folder is not known. */
  onOpenSent: (() => void) | null
  onKeep: () => void
  /** `null` while the form holds what the server refused: it would send the stored version instead. */
  onSendAnyway: (() => void) | null
}): ReactElement | null {
  const { t } = useTranslation(MAIL_NS)
  switch (state.kind) {
    case 'idle':
      return null
    case 'sending':
      return <p className="text-sm text-muted-foreground">{t('compose.sending')}</p>
    case 'sent':
      return <p className="rounded-lg border p-2 text-sm">{t('compose.alreadySent')}</p>
    case 'failed':
      return (
        <p className="rounded-lg border border-destructive/40 p-2 text-sm">
          {t('compose.notSent')} {state.code !== null && mailCodeMessage(t, state.code)}
        </p>
      )
    case 'refused':
      return (
        <p className="rounded-lg border border-destructive/40 p-2 text-sm">
          {composeErrorMessage(t, state.error, t('compose.notSent'), t('compose.invalidStored'))}
        </p>
      )
    case 'relocated':
      return <p className="rounded-lg border p-2 text-sm">{t('compose.changedElsewhere')}</p>
    case 'unknown':
      return (
        <div className="flex flex-col gap-2 rounded-lg border border-destructive/40 p-2 text-sm">
          <p>{t('compose.unknown')}</p>
          <div className="flex flex-wrap gap-2">
            {onOpenSent !== null && (
              <Button type="button" variant="outline" size="sm" onClick={onOpenSent}>
                {t('compose.openSent')}
              </Button>
            )}
            <Button type="button" variant="outline" size="sm" onClick={onKeep}>
              {t('compose.keepDraft')}
            </Button>
            {onSendAnyway !== null && (
              <Button type="button" variant="destructive" size="sm" onClick={onSendAnyway}>
                {t('compose.sendAnyway')}
              </Button>
            )}
          </div>
        </div>
      )
  }
}

/** How the last send ended, once its composer has closed. */
export function SentNotice(): ReactElement | null {
  const { t } = useTranslation(MAIL_NS)
  const { notice, dismissNotice } = useCompose()
  if (notice === null) {
    return null
  }
  return (
    <div className="flex items-start gap-2 rounded-lg border p-2 text-sm">
      <p className="min-w-0 flex-1 break-words">{sentMessage(t, notice)}</p>
      <Button type="button" variant="ghost" size="icon-xs" aria-label={t('triage.dismiss')} onClick={dismissNotice}>
        <X aria-hidden="true" />
      </Button>
    </div>
  )
}

/** The sends held for an Undo, once their composers have closed: each one's Undo while it can, else how it ended. */
export function HeldNotices(): ReactElement | null {
  const { held } = useCompose()
  if (held.length === 0) {
    return null
  }
  return (
    <>
      {held.map((notice) => (
        <HeldSend key={notice.send.scheduled_id} notice={notice} />
      ))}
    </>
  )
}

function HeldSend({ notice }: { notice: HeldNotice }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { undoSend, dismissHeld, start } = useCompose()
  const id = notice.send.scheduled_id
  if (notice.phase === 'ended') {
    const { ref } = notice.send
    return (
      <div className="flex items-start gap-2 rounded-lg border border-destructive/40 p-2 text-sm">
        <div className="flex min-w-0 flex-1 flex-col items-start gap-2">
          <p className="break-words">{heldEndMessage(t, notice.end)}</p>
          {notice.end.kind !== 'unknown' && (
            <Button
              type="button"
              variant="outline"
              size="xs"
              onClick={() => {
                dismissHeld(id)
                start({ kind: 'draft', ref })
              }}
            >
              {t('compose.openDraft')}
            </Button>
          )}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label={t('triage.dismiss')}
          onClick={() => {
            dismissHeld(id)
          }}
        >
          <X aria-hidden="true" />
        </Button>
      </div>
    )
  }
  const undoable = notice.phase === 'undoable' || notice.phase === 'undoing'
  return (
    <div className="flex items-center gap-2 rounded-lg border p-2 text-sm">
      <p className="min-w-0 flex-1 break-words">{notice.phase === 'tooLate' ? t('compose.tooLateToUndo') : t('compose.heldSending')}</p>
      {undoable && (
        <Button
          type="button"
          variant="outline"
          size="xs"
          aria-disabled={notice.phase === 'undoing'}
          onClick={() => {
            undoSend(id)
          }}
        >
          {t('compose.undo')}
        </Button>
      )}
    </div>
  )
}

/** The composer's news for screen readers, in regions mounted as long as the screen, like the toolbar's. */
export function ComposeAnnouncer(): ReactElement {
  const { announcement } = useCompose()
  return (
    <>
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {announcement?.urgent === false && announcement.text}
      </div>
      <div aria-live="assertive" aria-atomic="true" className="sr-only">
        {announcement?.urgent === true && announcement.text}
      </div>
    </>
  )
}

import { useEffect, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { useCompose, type HeldNotice } from './compose-context.ts'
import { MAIL_NS } from './i18n.ts'
import { heldEndMessage, recipientsLabel } from './messages.ts'
import { useToast } from './toast-context.ts'

/** The sends held for an Undo, once their composers have closed: each one a pinned toast until it is sent or dismissed. */
export function HeldToasts(): ReactElement {
  const { held } = useCompose()
  return (
    <>
      {held.map((notice) => (
        <HeldToast key={notice.send.scheduled_id} notice={notice} />
      ))}
    </>
  )
}

/** One held send's toast: Undo with the seconds left, then "too late", or how it ended with its draft to open. */
function HeldToast({ notice }: { notice: HeldNotice }): null {
  const { t } = useTranslation(MAIL_NS)
  const { pin } = useToast()
  const { undoSend, dismissHeld, start } = useCompose()
  const id = notice.send.scheduled_id
  const to = recipientsLabel(t, notice.to)

  useEffect(() => {
    if (notice.phase === 'ended') {
      const { ref } = notice.send
      const message = heldEndMessage(t, notice.end)
      pin(id, {
        message,
        spoken: message,
        tone: 'error',
        action:
          notice.end.kind === 'unknown'
            ? undefined
            : {
                label: t('compose.openDraft'),
                run: () => {
                  dismissHeld(id)
                  start({ kind: 'draft', ref })
                },
              },
        onDismiss: () => {
          dismissHeld(id)
        },
      })
      return
    }
    if (notice.phase === 'tooLate') {
      const message = t('compose.tooLateToUndo')
      pin(id, { message, spoken: message, tone: 'error' })
      return
    }
    if (notice.phase === 'sending') {
      pin(id, { message: t('compose.heldSending', { to }), spoken: null })
      return
    }
    // The countdown is seen, not heard: what is heard is said once, as Undo is offered.
    pin(id, {
      message: <Countdown until={notice.until} to={to} />,
      spoken: t('compose.heldSpoken', { to, count: Math.max(1, Math.ceil(notice.holdMs / 1_000)) }),
      action: {
        label: t('compose.undo'),
        busy: notice.phase === 'undoing',
        run: () => {
          undoSend(id)
        },
      },
    })
  }, [dismissHeld, id, notice, pin, start, t, to, undoSend])

  useEffect(
    () => () => {
      pin(id, null)
    },
    [id, pin],
  )
  return null
}

/** "Sending to … in 7 s", a second less each second until `until`. */
function Countdown({ until, to }: { until: number; to: string }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [now, setNow] = useState(() => Date.now())
  const left = until - now
  const seconds = Math.max(0, Math.ceil(left / 1_000))
  useEffect(() => {
    if (left <= 0) {
      return
    }
    // Just past the next whole second, when the number changes.
    const timer = setTimeout(
      () => {
        setNow(Date.now())
      },
      (left % 1_000 || 1_000) + 20,
    )
    return () => {
      clearTimeout(timer)
    }
  }, [left])
  return <>{t('compose.heldCountdown', { to, seconds })}</>
}

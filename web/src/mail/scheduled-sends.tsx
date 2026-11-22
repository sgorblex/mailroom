import { useEffect, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock } from 'lucide-react'

import { useAccountName } from '@/accounts/accounts-context.ts'
import { cancelScheduled, listScheduled, type ScheduledSend } from '@/api/mail.ts'
import { useTimeZone } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { Dialog } from '@/components/ui/dialog.tsx'
import { formatDateTime } from '@/lib/format.ts'

import { useCompose } from './compose-context.ts'
import { MAIL_NS } from './i18n.ts'
import { mailCodeMessage, scheduleErrorMessage } from './messages.ts'
import { SCHEDULED_KEY } from './queries.ts'

/** How often the Scheduled count is read again, as the worker sends what it holds. */
const REFRESH_MS = 60_000

/** The sidebar's Scheduled entry: how many sends are held, and the list of them, to cancel or edit. */
export function ScheduledEntry(): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const queryClient = useQueryClient()
  const { revision } = useCompose()
  const [open, setOpen] = useState(false)
  const query = useQuery({
    queryKey: SCHEDULED_KEY,
    queryFn: ({ signal }) => listScheduled(signal),
    refetchInterval: REFRESH_MS,
  })
  // A send, a held send's end or a closed composer may have changed what is held.
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: SCHEDULED_KEY })
  }, [revision, queryClient])
  const count = query.data?.length ?? 0
  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="justify-between px-2 font-normal"
        onClick={() => {
          void query.refetch()
          setOpen(true)
        }}
      >
        <span className="flex items-center gap-2">
          <Clock aria-hidden="true" />
          {t('scheduled.title')}
        </span>
        {count > 0 && (
          <span className="text-xs text-muted-foreground">
            <span aria-hidden="true">{count}</span>
            <span className="sr-only">{t('scheduled.count', { count })}</span>
          </span>
        )}
      </Button>
      {open && (
        <ScheduledDialog
          sends={query.data ?? null}
          failed={query.isError}
          onClose={() => {
            setOpen(false)
          }}
        />
      )}
    </>
  )
}

function ScheduledDialog({ sends, failed, onClose }: { sends: ScheduledSend[] | null; failed: boolean; onClose: () => void }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const queryClient = useQueryClient()
  const { start, canCompose } = useCompose()
  const [working, setWorking] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ text: string; alert: boolean } | null>(null)

  /** Cancels the held send; `edit` then opens its draft, which stays in Drafts either way. */
  async function cancel(send: ScheduledSend, edit: boolean): Promise<void> {
    setWorking(send.scheduled_id)
    setNotice(null)
    try {
      await cancelScheduled(send.scheduled_id)
      if (edit) {
        onClose()
        start({ kind: 'draft', ref: send.ref })
      } else {
        setNotice({ text: t('scheduled.cancelled'), alert: false })
      }
    } catch (error) {
      setNotice({ text: scheduleErrorMessage(t, error), alert: true })
    } finally {
      setWorking(null)
      void queryClient.invalidateQueries({ queryKey: SCHEDULED_KEY })
    }
  }

  let body: ReactElement
  if (sends === null) {
    body = failed ? (
      <p role="alert" className="text-sm">
        {t('scheduled.loadFailed')}
      </p>
    ) : (
      <p role="status" className="text-sm text-muted-foreground">
        {t('scheduled.loading')}
      </p>
    )
  } else if (sends.length === 0) {
    body = <p className="text-sm text-muted-foreground">{t('scheduled.empty')}</p>
  } else {
    body = (
      <ul className="flex flex-col divide-y">
        {sends.map((send) => (
          <ScheduledRow
            key={send.scheduled_id}
            send={send}
            busy={working !== null}
            editable={canCompose(send.account)}
            onCancel={() => void cancel(send, false)}
            onEdit={() => void cancel(send, true)}
          />
        ))}
      </ul>
    )
  }

  return (
    <Dialog label={t('scheduled.title')} size="lg" onCancel={onClose}>
      <h2 className="text-base font-semibold">{t('scheduled.title')}</h2>
      <div className="mt-3 max-h-[60vh] overflow-y-auto">{body}</div>
      <div role="status" className="mt-2 text-sm empty:hidden">
        {notice !== null && !notice.alert ? notice.text : null}
      </div>
      {notice?.alert === true && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {notice.text}
        </p>
      )}
      <div className="mt-4 flex justify-end">
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          {t('scheduled.close')}
        </Button>
      </div>
    </Dialog>
  )
}

function ScheduledRow({
  send,
  busy,
  editable,
  onCancel,
  onEdit,
}: {
  send: ScheduledSend
  busy: boolean
  editable: boolean
  onCancel: () => void
  onEdit: () => void
}): ReactElement {
  const { t, i18n } = useTranslation(MAIL_NS)
  const timeZone = useTimeZone()
  const accountName = useAccountName()
  const subject = send.subject === '' ? t('message.noSubject') : send.subject
  let state: string
  switch (send.state) {
    case 'held':
      state = t('scheduled.at', { when: formatDateTime(i18n.language, send.send_at, timeZone) })
      break
    case 'sending':
      state = t('compose.sending')
      break
    case 'failed':
      state = `${t('compose.notSent')} ${send.code === null ? '' : mailCodeMessage(t, send.code)}`.trim()
      break
  }
  return (
    <li className="flex flex-col gap-1 py-2 text-sm">
      <span className="font-medium break-words">{subject}</span>
      <span className="break-words text-muted-foreground">
        {t('scheduled.to', { recipients: send.recipients.join(', '), account: accountName(send.account) })}
      </span>
      {send.by === 'agent' && (
        <span className="break-words text-muted-foreground">
          {send.app === null ? t('scheduled.byAgent') : t('scheduled.byApp', { app: send.app })}
        </span>
      )}
      <span className={send.state === 'failed' ? 'text-destructive' : 'text-muted-foreground'}>{state}</span>
      {send.state !== 'sending' && (
        <div className="flex gap-2">
          {editable && (
            <Button
              type="button"
              variant="outline"
              size="xs"
              aria-label={t('scheduled.editLabel', { subject })}
              aria-disabled={busy}
              onClick={busy ? undefined : onEdit}
            >
              {t('scheduled.edit')}
            </Button>
          )}
          <Button
            type="button"
            variant="ghost"
            size="xs"
            aria-label={t('scheduled.cancelLabel', { subject })}
            aria-disabled={busy}
            onClick={busy ? undefined : onCancel}
          >
            {t('scheduled.cancel')}
          </Button>
        </div>
      )}
    </li>
  )
}

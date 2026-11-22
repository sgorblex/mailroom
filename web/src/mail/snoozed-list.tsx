import { useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlarmClock } from 'lucide-react'

import { useAccountName } from '@/accounts/accounts-context.ts'
import { listSnoozes, wakeSnooze, type Snooze } from '@/api/mail.ts'
import { useTimeZone } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { Dialog } from '@/components/ui/dialog.tsx'
import { formatDateTime } from '@/lib/format.ts'

import { MAIL_NS } from './i18n.ts'
import { mailCodeMessage, mailErrorMessage } from './messages.ts'
import { ALL_FOLDERS_KEY, ALL_MESSAGES_KEY, SNOOZES_KEY } from './queries.ts'

/** How often the Snoozed count is read again, as the worker brings messages back. */
const REFRESH_MS = 60_000

/** The sidebar's Snoozed entry: how many messages are snoozed, and the list of them, to bring back now. */
export function SnoozedEntry(): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [open, setOpen] = useState(false)
  const query = useQuery({
    queryKey: SNOOZES_KEY,
    queryFn: ({ signal }) => listSnoozes(signal),
    refetchInterval: REFRESH_MS,
  })
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
          <AlarmClock aria-hidden="true" />
          {t('snooze.list')}
        </span>
        {count > 0 && (
          <span className="text-xs text-muted-foreground">
            <span aria-hidden="true">{count}</span>
            <span className="sr-only">{t('snooze.count', { count })}</span>
          </span>
        )}
      </Button>
      {open && (
        <SnoozedDialog
          snoozes={query.data ?? null}
          failed={query.isError}
          onClose={() => {
            setOpen(false)
          }}
        />
      )}
    </>
  )
}

function SnoozedDialog({ snoozes, failed, onClose }: { snoozes: Snooze[] | null; failed: boolean; onClose: () => void }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const queryClient = useQueryClient()
  const [working, setWorking] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ text: string; alert: boolean } | null>(null)

  async function wake(snooze: Snooze): Promise<void> {
    setWorking(snooze.snooze_id)
    setNotice(null)
    try {
      await wakeSnooze(snooze.snooze_id)
      setNotice({ text: t('snooze.waking'), alert: false })
    } catch (error) {
      setNotice({ text: mailErrorMessage(t, error, t('snooze.wakeFailed')), alert: true })
    } finally {
      setWorking(null)
      void queryClient.invalidateQueries({ queryKey: SNOOZES_KEY })
      void queryClient.invalidateQueries({ queryKey: ALL_MESSAGES_KEY })
      void queryClient.invalidateQueries({ queryKey: ALL_FOLDERS_KEY })
    }
  }

  let body: ReactElement
  if (snoozes === null) {
    body = failed ? (
      <p role="alert" className="text-sm">
        {t('snooze.loadFailed')}
      </p>
    ) : (
      <p role="status" className="text-sm text-muted-foreground">
        {t('snooze.loading')}
      </p>
    )
  } else if (snoozes.length === 0) {
    body = <p className="text-sm text-muted-foreground">{t('snooze.empty')}</p>
  } else {
    body = (
      <ul className="flex flex-col divide-y">
        {snoozes.map((snooze) => (
          <SnoozedRow
            key={snooze.snooze_id}
            snooze={snooze}
            busy={working !== null}
            onWake={() => void wake(snooze)}
          />
        ))}
      </ul>
    )
  }

  return (
    <Dialog label={t('snooze.list')} size="lg" onCancel={onClose}>
      <h2 className="text-base font-semibold">{t('snooze.list')}</h2>
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

function SnoozedRow({ snooze, busy, onWake }: { snooze: Snooze; busy: boolean; onWake: () => void }): ReactElement {
  const { t, i18n } = useTranslation(MAIL_NS)
  const timeZone = useTimeZone()
  const accountName = useAccountName()
  const subject = snooze.subject === '' ? t('message.noSubject') : snooze.subject
  return (
    <li className="flex flex-col gap-1 py-2 text-sm">
      <span className="font-medium break-words">{subject}</span>
      <span className="break-words text-muted-foreground">
        {t('snooze.from', { sender: snooze.sender ?? '', account: accountName(snooze.account) })}
      </span>
      <span className="break-words text-muted-foreground">
        {t('snooze.back', { when: formatDateTime(i18n.language, snooze.until, timeZone), folder: snooze.folder })}
      </span>
      {snooze.code !== null && <span className="text-destructive">{`${t('snooze.notBack')} ${mailCodeMessage(t, snooze.code)}`}</span>}
      <div className="flex gap-2">
        <Button
          type="button"
          variant="outline"
          size="xs"
          aria-label={t('snooze.wakeLabel', { subject })}
          aria-disabled={busy}
          onClick={busy ? undefined : onWake}
        >
          {t('snooze.wake')}
        </Button>
      </div>
    </li>
  )
}

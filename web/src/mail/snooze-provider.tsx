import { useCallback, useMemo, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'

import { ApiRequestError } from '@/api/http.ts'
import { snoozeMessage, wakeSnooze } from '@/api/mail.ts'
import { useTimeZone } from '@/auth/auth-context.ts'
import { formatDateTime } from '@/lib/format.ts'

import { MAIL_NS } from './i18n.ts'
import { mailErrorMessage } from './messages.ts'
import { ALL_FOLDERS_KEY, ALL_MESSAGES_KEY, SNOOZES_KEY } from './queries.ts'
import { LaterDialog } from './send-later-dialog.tsx'
import { SnoozeContext, type Snoozing } from './snooze-context.ts'
import { useToast } from './toast-context.ts'
import { useTriage, type Target } from './triage-context.ts'

type Snoozed = Pick<Target, 'ref' | 'account_id'> & Partial<Pick<Target, 'listed_folder'>>

/**
 * Snoozes messages for the mail screen: asks until when, snoozes, closes the message when it was
 * open, reads the lists again, and says so in a toast whose Undo brings it back at once.
 */
export function SnoozeProvider({ children }: { children: ReactNode }): ReactElement {
  const { t, i18n } = useTranslation(MAIL_NS)
  const toast = useToast()
  const triage = useTriage()
  const timeZone = useTimeZone()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [asking, setAsking] = useState<Snoozed | null>(null)
  const pending = useRef(new Set<string>())

  const reload = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ALL_MESSAGES_KEY })
    void queryClient.invalidateQueries({ queryKey: ALL_FOLDERS_KEY })
    void queryClient.invalidateQueries({ queryKey: SNOOZES_KEY })
  }, [queryClient])

  const wake = useCallback(
    (snoozeId: string) => {
      wakeSnooze(snoozeId)
        .then(() => {
          toast.show({ message: t('snooze.waking') })
        })
        .catch((error: unknown) => {
          toast.show({ message: mailErrorMessage(t, error, t('snooze.wakeFailed')), tone: 'error' })
        })
        .finally(reload)
    },
    [reload, t, toast],
  )

  const snooze = useCallback(
    (message: Snoozed, until: Date) => {
      const { ref } = message
      if (pending.current.has(ref)) {
        return
      }
      pending.current.add(ref)
      const open = triage.open
      const listed = message.listed_folder ?? (open?.ref === ref ? open.listed_folder : undefined)
      snoozeMessage(ref, until, listed)
        .then((snoozed) => {
          if (triage.open?.ref === ref) {
            triage.setOpen(null)
            void navigate({ to: '/mail', search: (previous) => ({ ...previous, message: undefined, thread: undefined }) })
          }
          toast.show({
            message: t('snooze.snoozed', { when: formatDateTime(i18n.language, snoozed.until, timeZone) }),
            action: {
              label: t('undo.undo'),
              run: () => {
                wake(snoozed.snooze_id)
              },
            },
          })
        })
        .catch((error: unknown) => {
          const message =
            error instanceof ApiRequestError && error.code === 'already_snoozed'
              ? t('snooze.already')
              : mailErrorMessage(t, error, t('snooze.failed'))
          toast.show({ message, tone: 'error' })
        })
        .finally(() => {
          pending.current.delete(ref)
          reload()
        })
    },
    [i18n.language, navigate, reload, t, timeZone, toast, triage, wake],
  )

  const snoozing = useMemo<Snoozing>(() => ({ snooze: setAsking }), [])

  return (
    <SnoozeContext value={snoozing}>
      {children}
      {asking !== null && (
        <LaterDialog
          title={t('snooze.title')}
          presetLabel={(kind) => t(`snooze.later.${kind}`)}
          pickLabel={t('snooze.pick')}
          tooFar={t('snooze.tooFar')}
          onPick={(at) => {
            setAsking(null)
            snooze(asking, at)
          }}
          onCancel={() => {
            setAsking(null)
          }}
        />
      )}
    </SnoozeContext>
  )
}

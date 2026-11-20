import { useCallback, useMemo, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { useAccountName } from '@/accounts/accounts-context.ts'
import { ApiRequestError } from '@/api/http.ts'
import { muteThread, unmuteThread, type MutedThread } from '@/api/mail.ts'

import { MAIL_NS } from './i18n.ts'
import { mailErrorMessage } from './messages.ts'
import { MuteContext, type Muting } from './mute-context.ts'
import { useToast } from './toast-context.ts'

/**
 * Mutes and unmutes conversations for the mail screen, saying so in a toast: a mute with Undo, and
 * that it has no effect while the account's Run filters is off. One request per message at a time.
 */
export function MuteProvider({ children }: { children: ReactNode }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const toast = useToast()
  const accountName = useAccountName()
  const [muted, setMuted] = useState<ReadonlyMap<string, MutedThread>>(new Map())
  const pending = useRef(new Set<string>())

  const settle = useCallback((ref: string, thread: MutedThread) => {
    setMuted((previous) => new Map(previous).set(ref, thread))
  }, [])

  /** The thread is unmuted: every message shown as muted by it says so no more. */
  const forget = useCallback((thread: MutedThread) => {
    setMuted((previous) => new Map([...previous].filter(([, each]) => each.id !== thread.id)))
  }, [])

  const unmuteRef = useCallback(
    async (ref: string, thread: MutedThread) => {
      if (pending.current.has(ref)) {
        return
      }
      pending.current.add(ref)
      try {
        await unmuteThread(thread.id)
        forget(thread)
        toast.show({ message: t('mute.unmuted') })
      } catch (error) {
        // A 404: unmuted elsewhere already, which is what was asked.
        if (error instanceof ApiRequestError && error.status === 404) {
          forget(thread)
          toast.show({ message: t('mute.unmuted') })
        } else {
          toast.show({ message: mailErrorMessage(t, error, t('mute.unmuteFailed')), tone: 'error' })
        }
      } finally {
        pending.current.delete(ref)
      }
    },
    [forget, t, toast],
  )

  const muting = useMemo<Muting>(
    () => ({
      mutedAs: (ref) => muted.get(ref),
      mute: ({ ref, account_id: account }) => {
        if (pending.current.has(ref)) {
          return
        }
        pending.current.add(ref)
        void muteThread(ref)
          .then((thread) => {
            settle(ref, thread)
            const parts = [t('mute.muted')]
            if (!thread.run_filters) {
              parts.push(t('mute.runFiltersOff', { account: accountName(account) }))
            }
            toast.show({
              message: parts.join(' '),
              action: {
                label: t('undo.undo'),
                run: () => void unmuteRef(ref, thread),
              },
            })
          })
          .catch((error: unknown) => {
            const message =
              error instanceof ApiRequestError && error.code === 'nothing_to_mute'
                ? t('mute.nothingToMute')
                : mailErrorMessage(t, error, t('mute.failed'))
            toast.show({ message, tone: 'error' })
          })
          .finally(() => {
            pending.current.delete(ref)
          })
      },
      unmute: ({ ref }) => {
        const thread = muted.get(ref)
        if (thread !== undefined) {
          void unmuteRef(ref, thread)
        }
      },
    }),
    [muted, settle, t, toast, accountName, unmuteRef],
  )

  return <MuteContext value={muting}>{children}</MuteContext>
}

import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { readNotificationFolders, type AccountNotifications, type LiveEvent } from '@/api/mail.ts'

import { useLiveEvents, type Viewing } from './live-context.ts'
import { mayNotify, notifies, showNotification } from './notification-choice.ts'
import { NOTIFICATIONS_KEY } from './queries.ts'
import { useFocusedViewing } from './viewing-channel.ts'

/** How long the folder choice read for a notification is used before it is read again. */
const CHOICE_STALE_MS = 60_000

/**
 * New mail in a folder the user chose, as desktop notifications while this tab is open and
 * allowed to show them: one per message the event names (the newest 3), their sender and subject
 * only, and "N new messages" when there were more; none for the folder the focused tab shows,
 * this one or another (`useFocusedViewing`).
 * Clicking one focuses the tab and opens the message (the folder, for the summary).
 */
export function DesktopNotifications({ viewing }: { viewing: Viewing | null }): ReactElement | null {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const seen = useFocusedViewing(viewing)

  async function notify(event: Extract<LiveEvent, { type: 'new_mail' }>): Promise<void> {
    if (!mayNotify() || seen(event.account, event.folder)) {
      return
    }
    let chosen: AccountNotifications[]
    try {
      chosen = await queryClient.query({
        queryKey: NOTIFICATIONS_KEY,
        queryFn: ({ signal }) => readNotificationFolders(signal),
        staleTime: CHOICE_STALE_MS,
      })
    } catch {
      return
    }
    if (!notifies(chosen, event.account, event.folder)) {
      return
    }
    const show = (title: string, options: NotificationOptions, message?: string): boolean => {
      const notification = showNotification(title, options)
      notification?.addEventListener('click', (click) => {
        click.preventDefault()
        window.focus()
        notification.close()
        void navigate({ to: '/mail', search: { account: event.account, folder: event.folder, message } })
      })
      return notification !== null
    }
    for (const message of event.messages) {
      // The tag keeps another open tab from showing the same message twice.
      const shown = show(
        message.sender === '' ? t('desktopNotifications.unknownSender') : message.sender,
        { body: message.subject === '' ? t('desktopNotifications.noSubject') : message.subject, tag: `mailroom:${message.ref}` },
        message.ref,
      )
      if (!shown) {
        return
      }
    }
    if (event.count > event.messages.length) {
      show(t('desktopNotifications.summary', { count: event.count }), {
        tag: `mailroom:${String(event.account)}:${event.folder}`,
      })
    }
  }

  useLiveEvents((event) => {
    if (event.type === 'new_mail') {
      void notify(event)
    }
  })
  return null
}

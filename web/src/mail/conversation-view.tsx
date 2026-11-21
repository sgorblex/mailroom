import { useEffect, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'

import { readThread } from '@/api/mail.ts'
import { useTimeZone } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { cn } from '@/lib/utils.ts'

import { isConversation } from './conversations.ts'
import { useLiveEvents } from './live-context.ts'
import { addressLabel, formatListDate } from './format.ts'
import { MAIL_NS } from './i18n.ts'
import { MessageView } from './message-view.tsx'
import { mailErrorMessage } from './messages.ts'
import { READ_ON_MOUNT, threadKey } from './queries.ts'
import { useTriage, type Target } from './triage-context.ts'

function conversationRow(rows: Target[], ref: string): Target | undefined {
  return rows.find((each) => each.ref === ref && isConversation(each))
}

/**
 * A conversation opened from its row (`threadRef`, its latest message): its messages, read live,
 * oldest first, each to read in turn, above the one being read (`messageRef`).
 */
export function ConversationView({ threadRef, messageRef }: { threadRef: string; messageRef: string }): ReactElement {
  const { t, i18n } = useTranslation(MAIL_NS)
  const timeZone = useTimeZone()
  const { visibleRows, subscribeVisible } = useTriage()
  const queryClient = useQueryClient()
  const thread = useQuery({
    queryKey: threadKey(threadRef),
    queryFn: ({ signal }) => readThread(threadRef, signal),
    ...READ_ON_MOUNT,
  })
  // The list's row, which the message stands for while it is the one read: found once the list
  // shows it, which a link followed into the screen may load after this view.
  const [row, setRow] = useState<Target | undefined>(() => conversationRow(visibleRows(), threadRef))
  useEffect(
    () =>
      subscribeVisible((rows) => {
        const found = conversationRow(rows, threadRef)
        if (found !== undefined) {
          setRow((current) => current ?? found)
        }
      }),
    [subscribeVisible, threadRef],
  )
  const messages = thread.status === 'success' ? thread.data.messages.toReversed() : []
  // A change to one of its accounts' folders may have brought a reply, or moved a message.
  useLiveEvents((event) => {
    if (event.type !== 'sync' && messages.some((message) => message.account_id === event.account)) {
      void queryClient.invalidateQueries({ queryKey: threadKey(threadRef) })
    }
  })

  return (
    <div className="flex flex-1 flex-col gap-4">
      <nav aria-label={t('conversation.label')} className="flex flex-col gap-2">
        {thread.status === 'pending' && (
          <p role="status" className="text-sm text-muted-foreground">
            {t('conversation.loading')}
          </p>
        )}
        {thread.status === 'error' && (
          <div className="flex flex-col items-start gap-2">
            <p role="alert" className="text-sm">
              {mailErrorMessage(t, thread.error, t('conversation.loadFailed'))}
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void thread.refetch()
              }}
            >
              {t('auth.retry')}
            </Button>
          </div>
        )}
        {thread.status === 'success' && (
          <>
            <p className="text-sm text-muted-foreground">{t('conversation.count', { count: messages.length })}</p>
            <ol className="flex flex-col divide-y rounded-lg border">
              {messages.map((message) => {
                const sender = addressLabel(message.from)
                const current = message.ref === messageRef
                return (
                  <li key={message.ref}>
                    <Link
                      to="/mail"
                      search={(previous) => ({ ...previous, message: message.ref })}
                      aria-current={current ? 'true' : undefined}
                      className={cn('flex min-w-0 flex-col gap-0.5 px-3 py-2 text-sm hover:bg-accent', current && 'bg-accent')}
                    >
                      <span className="flex items-baseline gap-2">
                        <span className={cn('min-w-0 flex-1 truncate', !message.flags.seen && 'font-semibold')}>
                          {!message.flags.seen && <span className="sr-only">{`${t('list.unread')}: `}</span>}
                          {sender === '' ? t('message.noSender') : sender}
                        </span>
                        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                          {formatListDate(i18n.language, message.date, timeZone)}
                        </span>
                      </span>
                      {message.preview !== '' && <span className="line-clamp-1 text-muted-foreground">{message.preview}</span>}
                    </Link>
                  </li>
                )
              })}
            </ol>
          </>
        )}
      </nav>
      <MessageView key={messageRef} messageRef={messageRef} conversation={row} />
    </div>
  )
}

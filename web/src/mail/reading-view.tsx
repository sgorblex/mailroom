import { useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { skipToken, useQuery } from '@tanstack/react-query'

import { cn } from '@/lib/utils.ts'

import { ConversationView } from './conversation-view.tsx'
import { MAIL_NS } from './i18n.ts'
import { MessageView } from './message-view.tsx'
import { messageQuery, threadQuery } from './queries.ts'

interface Opened {
  message: string
  thread: string | undefined
}

/**
 * The open message (`message`), or the conversation it is read in (`thread`). Another one opened
 * (a row, j/k) is read first: until it answers, the one shown stays, dimmed, out of reach and
 * busy, a screen reader told the next is loading; the first one opened shows its own loading line.
 */
export function ReadingView({ message, thread }: Opened): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [shown, setShown] = useState<Opened>({ message, thread })
  // The very reads the views make, shared with them: nothing is asked twice.
  const read = useQuery(messageQuery(message, false))
  const conversation = useQuery(
    thread === undefined ? { queryKey: ['no-thread'], queryFn: skipToken } : threadQuery(thread),
  )
  const ready = read.status !== 'pending' && (thread === undefined || conversation.status !== 'pending')
  const current = shown.message === message && shown.thread === thread
  if (ready && !current) {
    setShown({ message, thread })
  }
  const leaving = !current && !ready

  return (
    <div
      aria-busy={leaving}
      inert={leaving}
      className={cn('flex flex-1 flex-col', leaving && 'opacity-50 motion-safe:transition-opacity motion-safe:delay-150')}
    >
      <p role="status" className="sr-only">
        {leaving ? t('message.loading') : ''}
      </p>
      {shown.thread === undefined ? (
        <MessageView key={shown.message} messageRef={shown.message} leaving={leaving} />
      ) : (
        <ConversationView key={shown.thread} threadRef={shown.thread} messageRef={shown.message} leaving={leaving} />
      )}
    </div>
  )
}

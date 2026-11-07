import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearch } from '@tanstack/react-router'
import { Paperclip, Star } from 'lucide-react'

import type { MessageItem, MessagePage, SourceFailure } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { useLoad } from '@/lib/use-load.ts'
import { useUnmountSignal } from '@/lib/use-unmount-signal.ts'
import { cn } from '@/lib/utils.ts'

import { addressLabel, formatListDate } from './format.ts'
import { MAIL_NS } from './i18n.ts'
import { ACCOUNT_NOT_FOUND, mailCodeMessage, mailErrorMessage } from './messages.ts'
import { useTriage, type Target } from './triage-context.ts'

type More = { kind: 'idle' } | { kind: 'loading' } | { kind: 'failed'; message: string }

/**
 * A folder's messages or a search's, newest first, a page at a time: "Load more" asks for the
 * next one with the cursor the last page gave. Mounted anew (by key) for each source.
 */
export function MessageList({
  loadPage,
  searching,
  reloadKey,
}: {
  loadPage: (cursor: string | null, signal: AbortSignal) => Promise<MessagePage>
  searching: boolean
  /** A new value reads the list again from its first page, keeping it on show meanwhile. */
  reloadKey?: unknown
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const loadFirst = useCallback((signal: AbortSignal) => loadPage(null, signal), [loadPage])
  const { state, retry } = useLoad(loadFirst, (error) => mailErrorMessage(t, error, t('list.loadFailed'), ACCOUNT_NOT_FOUND), reloadKey)
  const [later, setLater] = useState<MessagePage[]>([])
  const [more, setMore] = useState<More>({ kind: 'idle' })
  const [readFor, setReadFor] = useState(reloadKey)
  if (readFor !== reloadKey) {
    setReadFor(reloadKey)
    setLater([])
  }
  const unmountSignal = useUnmountSignal()
  const triage = useTriage()
  const { removed, setVisible } = triage
  const loaded = state.kind === 'loaded' ? [state.value, ...later] : []
  // Rows moved away since are gone in place.
  const messages = loaded.flatMap((page) => page.messages).filter((message) => !removed.has(message.ref))
  useEffect(() => {
    setVisible(messages)
  })
  useEffect(
    () => () => {
      setVisible([])
    },
    [setVisible],
  )

  if (state.kind === 'loading') {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        {t('list.loading')}
      </p>
    )
  }
  if (state.kind === 'failed') {
    return (
      <div className="flex flex-col items-start gap-3">
        <p role="alert" className="text-sm">
          {state.message}
        </p>
        <Button variant="outline" size="sm" onClick={retry}>
          {t('auth.retry')}
        </Button>
      </div>
    )
  }

  const failures = uniqueFailures(loaded.flatMap((page) => page.failures))
  const nextCursor = loaded[loaded.length - 1]?.next_cursor ?? null

  async function loadMore(cursor: string): Promise<void> {
    setMore({ kind: 'loading' })
    const signal = unmountSignal()
    try {
      const page = await loadPage(cursor, signal)
      setLater((value) => [...value, page])
      setMore({ kind: 'idle' })
    } catch (error) {
      if (!signal.aborted) {
        setMore({ kind: 'failed', message: mailErrorMessage(t, error, t('list.loadFailed'), ACCOUNT_NOT_FOUND) })
      }
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {failures.length > 0 && <Failures failures={failures} />}
      {/* Every loaded row moved away is not an empty folder while there is more to load. */}
      {messages.length === 0 ? (
        nextCursor === null && (
          <p className="text-sm text-muted-foreground">{searching ? t('list.noMatches') : t('list.empty')}</p>
        )
      ) : (
        <>
          <SelectAll messages={messages} />
          <ul aria-label={searching ? t('list.results') : t('list.messages')} className="flex flex-col divide-y rounded-lg border">
            {messages.map((message) => (
              <li key={message.ref} className="flex items-start">
                <input
                  type="checkbox"
                  aria-label={t('triage.select', { subject: message.subject === '' ? t('message.noSubject') : message.subject })}
                  className="m-3 mr-0 size-4 shrink-0 accent-primary"
                  checked={triage.selection.has(message.ref)}
                  onChange={(event) => {
                    triage.select([message], event.target.checked)
                  }}
                />
                <MessageRow message={message} showSource={searching} />
              </li>
            ))}
          </ul>
        </>
      )}
      {more.kind === 'failed' && (
        <p role="alert" className="text-sm text-destructive">
          {more.message}
        </p>
      )}
      {nextCursor !== null && (
        <Button
          variant="outline"
          size="sm"
          className="self-center"
          aria-disabled={more.kind === 'loading'}
          onClick={() => {
            if (more.kind !== 'loading') {
              void loadMore(nextCursor)
            }
          }}
        >
          {more.kind === 'loading' ? t('list.loadingMore') : t('list.loadMore')}
        </Button>
      )}
    </div>
  )
}

/** Each source once: a later page reports the same failing source again. */
function uniqueFailures(failures: SourceFailure[]): SourceFailure[] {
  const seen = new Set<string>()
  return failures.filter((failure) => {
    const key = JSON.stringify([failure.account_id, failure.folder, failure.code])
    if (seen.has(key)) {
      return false
    }
    seen.add(key)
    return true
  })
}

function Failures({ failures }: { failures: SourceFailure[] }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  return (
    <div role="alert" className="flex flex-col gap-1 rounded-lg border border-destructive/40 p-3 text-sm">
      <p className="font-medium">{t('list.partial')}</p>
      <ul className="flex flex-col gap-0.5">
        {failures.map((failure) => (
          <li key={JSON.stringify([failure.account_id, failure.folder, failure.code])}>
            {failure.folder === null
              ? t('list.failedAccount', { account: failure.account_id, reason: mailCodeMessage(t, failure.code, ACCOUNT_NOT_FOUND) })
              : t('list.failedFolder', {
                  account: failure.account_id,
                  folder: failure.folder,
                  reason: mailCodeMessage(t, failure.code, ACCOUNT_NOT_FOUND),
                })}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Selects every loaded row, or none once all are. */
function SelectAll({ messages }: { messages: Target[] }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { selection, select } = useTriage()
  const box = useRef<HTMLInputElement>(null)
  const chosen = messages.filter((message) => selection.has(message.ref)).length
  const all = chosen === messages.length
  useEffect(() => {
    if (box.current !== null) {
      box.current.indeterminate = chosen > 0 && !all
    }
  }, [chosen, all])
  return (
    <label className="flex items-center gap-3 px-3 text-sm text-muted-foreground">
      <input
        ref={box}
        type="checkbox"
        className="size-4 accent-primary"
        checked={all}
        onChange={() => {
          select(messages, !all)
        }}
      />
      {t('triage.selectAll')}
    </label>
  )
}

function MessageRow({ message, showSource }: { message: MessageItem; showSource: boolean }): ReactElement {
  const { t, i18n } = useTranslation(MAIL_NS)
  const { message: open } = useSearch({ from: '/mail' })
  const flags = useTriage().flagsOf(message)
  const unread = !flags.seen
  const subject = message.subject === '' ? t('message.noSubject') : message.subject
  const sender = addressLabel(message.from)
  return (
    <Link
      to="/mail"
      search={(previous) => ({ ...previous, message: message.ref })}
      aria-current={message.ref === open ? 'true' : undefined}
      className={cn(
        'flex min-w-0 flex-1 flex-col gap-0.5 p-3 text-sm hover:bg-accent',
        message.ref === open && 'bg-accent',
      )}
    >
      <span className="flex items-baseline gap-2">
        <span className={cn('min-w-0 flex-1 truncate', unread && 'font-semibold')}>
          {sender === '' ? t('message.noSender') : sender}
        </span>
        {flags.flagged && (
          <Star role="img" aria-label={t('list.flagged')} className="size-3.5 shrink-0 self-center fill-current" />
        )}
        {message.has_attachments && (
          <Paperclip role="img" aria-label={t('list.hasAttachments')} className="size-3.5 shrink-0 self-center" />
        )}
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
          {formatListDate(i18n.language, message.date)}
        </span>
      </span>
      <span className={cn('truncate', unread ? 'font-semibold' : 'text-foreground')}>
        {unread && <span className="sr-only">{`${t('list.unread')}: `}</span>}
        {subject}
      </span>
      {message.preview !== '' && (
        <span className="line-clamp-1 text-muted-foreground">{message.preview}</span>
      )}
      {showSource && (
        <span className="truncate text-xs text-muted-foreground">
          {`${message.account_id} · ${message.folder}`}
        </span>
      )}
    </Link>
  )
}

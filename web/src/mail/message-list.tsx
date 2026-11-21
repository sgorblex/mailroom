import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearch } from '@tanstack/react-router'
import { Bookmark, Forward, Paperclip, Reply, Star } from 'lucide-react'

import { AccountName } from '@/accounts/account-name.tsx'
import { useAccountKeywords, useAccountLabel, useAccountName } from '@/accounts/accounts-context.ts'
import type { MatchScope, MessageItem, MessagePage, SourceFailure } from '@/api/mail.ts'
import { useTimeZone } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { Menu, type MenuItem, type MenuPoint } from '@/components/ui/menu.tsx'
import { useUnmountSignal } from '@/lib/use-unmount-signal.ts'
import { cn } from '@/lib/utils.ts'

import { listedActions, unavailableReason, type ActionContext } from './action-registry.ts'
import { useCompose } from './compose-context.ts'
import { useDraftFilter } from './filter-draft.ts'
import { useMuting } from './mute-context.ts'
import { addressLabel, formatListDate } from './format.ts'
import { MAIL_NS } from './i18n.ts'
import { endMessageDrag, startMessageDrag } from './message-drag.ts'
import { ACCOUNT_NOT_FOUND, mailCodeMessage, mailErrorMessage } from './messages.ts'
import { keptRows, keptTail, withKept, type Kept, type Tail } from './list-tail.ts'
import { fetchPage, messagesKey, READ_ON_MOUNT } from './queries.ts'
import { SelectMatching } from './select-matching.tsx'
import { TOAST_RETURN_ID } from './toast.tsx'
import { useTriageActions } from './triage-actions.ts'
import { useTriage, type Flags, type Target } from './triage-context.ts'

type More = { kind: 'idle' } | { kind: 'loading' } | { kind: 'failed'; message: string }

/**
 * A folder's messages or a search's, newest first, a page at a time: "Load more" asks for the
 * next one with the cursor the last page gave. Mounted anew (by key) for each source. Each row
 * names where it is: its account and folder in a search, its folder in a folder's subfolders.
 * Invalidating its first page (`messagesKey`) reads the list again, keeping it on show meanwhile:
 * new rows come in at the top, and the rows loaded below it stay. Under quick filters, a row this
 * screen changed so it no longer matches stays too, until the source changes.
 */
export function MessageList({
  source,
  loadPage,
  match,
  searching,
  filtered = false,
  subfolders = false,
}: {
  /** Names the list, for its pages' query keys. */
  source: string
  loadPage: (cursor: string | null, signal: AbortSignal) => Promise<MessagePage>
  /** What the list shows as a search, for acting on every message it matches; without it, only loaded rows are. */
  match?: MatchScope
  searching: boolean
  /** Narrowed by quick filters: an empty list is no match, not an empty folder. */
  filtered?: boolean
  /** Read with its subfolders. */
  subfolders?: boolean
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const queryClient = useQueryClient()
  const first = useQuery({
    queryKey: messagesKey(source, null),
    queryFn: ({ signal }) => loadPage(null, signal),
    ...READ_ON_MOUNT,
  })
  const [tail, setTail] = useState<Tail | null>(null)
  const [more, setMore] = useState<More>({ kind: 'idle' })
  const triage = useTriage()
  const { removed, setVisible, refreshed, flagsOf } = triage
  // Under quick filters, a row this screen changed so it no longer matches (read on opening it
  // under Unread) stays on show, read again too, until the source changes.
  const [kept, setKept] = useState<readonly Kept[]>([])
  const shown = (page: MessagePage | undefined, rows: Tail | null): MessageItem[] =>
    withKept([...(page?.messages ?? []), ...(rows?.rows ?? [])], kept)
  // The first page read again: what was loaded after it stays below it.
  const [read, setRead] = useState({ at: first.dataUpdatedAt, page: first.data })
  if (read.at !== first.dataUpdatedAt) {
    const nextTail = keptTail(read.page, tail, first.data)
    setRead({ at: first.dataUpdatedAt, page: first.data })
    setTail(nextTail)
    if (filtered) {
      setKept(
        keptRows(
          shown(read.page, tail).filter((message) => !removed.has(message.ref)),
          shown(first.data, nextTail),
          (message) => changedHere(message, flagsOf(message)),
        ),
      )
    }
  }
  const latestFirst = useRef(first.data)
  useEffect(() => {
    latestFirst.current = first.data
  })
  const [menu, setMenu] = useState<{ row: MessageItem; anchor: HTMLElement; point: MenuPoint } | null>(null)
  const closeMenu = useCallback(() => {
    setMenu(null)
  }, [])
  const unmountSignal = useUnmountSignal()
  const page = first.status === 'success' ? first.data : null
  // Rows moved away since are gone in place.
  const messages = (page === null ? [] : shown(page, tail)).filter((message) => !removed.has(message.ref))
  useEffect(() => {
    setVisible(messages)
  })
  useEffect(() => {
    if (page !== null) {
      refreshed(source, page.messages)
    }
  }, [page, source, refreshed])
  useEffect(
    () => () => {
      setVisible([])
    },
    [setVisible],
  )

  if (first.status === 'pending') {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        {t('list.loading')}
      </p>
    )
  }
  if (first.status === 'error') {
    return (
      <div className="flex flex-col items-start gap-3">
        <p role="alert" className="text-sm">
          {mailErrorMessage(t, first.error, t('list.loadFailed'), ACCOUNT_NOT_FOUND)}
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            void first.refetch()
          }}
        >
          {t('auth.retry')}
        </Button>
      </div>
    )
  }

  const failures = uniqueFailures([...first.data.failures, ...(tail?.failures ?? [])])
  const nextCursor = tail === null ? first.data.next_cursor : tail.cursor
  // Every loaded row selected, and more to load: the rest of what the list matches can be.
  const allLoaded = messages.length > 0 && messages.every((message) => triage.selection.has(message.ref))
  const offerMatching = match !== undefined && (triage.matching !== null || (allLoaded && nextCursor !== null))

  async function loadMore(cursor: string): Promise<void> {
    setMore({ kind: 'loading' })
    const signal = unmountSignal()
    try {
      const next = await fetchPage(queryClient, messagesKey(source, cursor), (querySignal) => loadPage(cursor, querySignal), signal)
      setTail((value) =>
        // The first page read again meanwhile no longer goes on at `cursor`: the page is stale.
        (value === null ? latestFirst.current?.next_cursor : value.cursor) !== cursor
          ? value
          : {
              rows: [...(value?.rows ?? []), ...next.messages],
              failures: [...(value?.failures ?? []), ...next.failures],
              cursor: next.next_cursor,
            },
      )
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
          <p className="text-sm text-muted-foreground">{searching || filtered ? t('list.noMatches') : t('list.empty')}</p>
        )
      ) : (
        <>
          <SelectAll messages={messages} />
          {offerMatching && <SelectMatching loaded={messages.length} scope={match} />}
          <ul id={TOAST_RETURN_ID} aria-label={searching ? t('list.results') : t('list.messages')} className="flex flex-col divide-y rounded-lg border">
            {messages.map((message) => (
              // The row's link is what takes focus: its menu key and a right-click reach it from here.
              // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
              <li
                key={message.ref}
                className="group/row flex items-start"
                // Dragged onto a folder, it moves the selection when it is in it (as every row is while
                // every matching message is selected), else itself alone.
                draggable
                onDragStart={(event) => {
                  const inSelection = triage.matching !== null || triage.selection.has(message.ref)
                  const targets = inSelection ? [...triage.selection.values()] : [message]
                  startMessageDrag(event.dataTransfer, targets, t('list.dragging', { count: targets.length }))
                }}
                onDragEnd={endMessageDrag}
                onContextMenu={(event) => {
                  const anchor = event.currentTarget.querySelector('a')
                  // Shift+right-click keeps the browser's own menu.
                  if (event.shiftKey || anchor === null) {
                    return
                  }
                  event.preventDefault()
                  setMenu({ row: message, anchor, point: { x: event.clientX, y: event.clientY } })
                }}
                onKeyDown={(event) => {
                  const anchor = event.currentTarget.querySelector('a')
                  if (anchor !== null && (event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey))) {
                    event.preventDefault()
                    const rect = anchor.getBoundingClientRect()
                    setMenu({ row: message, anchor, point: { x: rect.left, y: rect.bottom } })
                  }
                }}
              >
                <input
                  type="checkbox"
                  name="message"
                  value={message.ref}
                  aria-label={t('triage.select', { subject: message.subject === '' ? t('message.noSubject') : message.subject })}
                  className="m-3 mr-0 size-4 shrink-0 accent-primary"
                  checked={triage.matching !== null || triage.selection.has(message.ref)}
                  onChange={(event) => {
                    triage.select([message], event.target.checked)
                  }}
                />
                <StarToggle message={message} />
                <ToSortToggle message={message} />
                <MessageRow message={message} source={searching ? 'account' : subfolders ? 'folder' : null} />
              </li>
            ))}
          </ul>
        </>
      )}
      {menu !== null && (
        <MessageMenu
          key={`${menu.row.ref} ${String(menu.point.x)} ${String(menu.point.y)}`}
          row={menu.row}
          anchor={menu.anchor}
          point={menu.point}
          onClose={closeMenu}
        />
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

/**
 * The context menu of a row: the registry's actions on the selection when the row is in it, else
 * on the row alone. A picker it opens acts on the selection, so an unselected row becomes it.
 */
function MessageMenu({
  row,
  anchor,
  point,
  onClose,
}: {
  row: MessageItem
  anchor: HTMLElement
  point: MenuPoint
  onClose: () => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const triage = useTriage()
  const compose = useCompose()
  const draftFilter = useDraftFilter()
  const muting = useMuting()
  // Every row is selected, as its checkbox shows, while every matching message is.
  const selected = triage.matching !== null || triage.selection.has(row.ref)
  const targets = selected ? [...triage.selection.values()] : [row]
  // On the selection, the toolbar's actions: on every matching message when they are selected.
  const actions = useTriageActions(selected ? undefined : targets)
  const [only] = targets
  const context: ActionContext = {
    actions,
    message: targets.length === 1 && only !== undefined ? only : null,
    account: null,
    compose,
    openPicker: (picker) => {
      if (!selected) {
        triage.clearSelection()
        triage.select([row], true)
      }
      if (picker === 'move') {
        triage.setPicker(true)
      } else {
        triage.setAccountPicker(true)
      }
    },
    draftFilter,
    muting,
  }
  const items: MenuItem[] = [
    ...listedActions('reply', context),
    ...listedActions('message', context),
    ...listedActions('triage', context),
  ].map((action) => {
    const reason = action.group === 'triage' && triage.busy ? 'triage.busy' : unavailableReason(action, context)
    return {
      label: t(action.label(context)),
      shortcut: action.shortcut,
      disabledReason: reason === null ? null : t(reason),
      destructive: action.destructive,
      onSelect: () => {
        action.run(context)
      },
    }
  })
  return (
    <Menu
      label={t('list.menu', { subject: row.subject === '' ? t('message.noSubject') : row.subject })}
      anchor={anchor}
      point={point}
      items={items}
      onClose={onClose}
    />
  )
}

/** This screen changed a flag of `message` since it was read. */
function changedHere(message: MessageItem, flags: Flags): boolean {
  return flags.seen !== message.flags.seen || flags.flagged !== message.flags.flagged || flags.to_sort !== message.flags.to_sort
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
  const accountName = useAccountName()
  return (
    <div role="alert" className="flex flex-col gap-1 rounded-lg border border-destructive/40 p-3 text-sm">
      <p className="font-medium">{t('list.partial')}</p>
      <ul className="flex flex-col gap-0.5">
        {failures.map((failure) => (
          <li key={JSON.stringify([failure.account_id, failure.folder, failure.code])}>
            {failure.folder === null
              ? t('list.failedAccount', { account: accountName(failure.account_id), reason: mailCodeMessage(t, failure.code, ACCOUNT_NOT_FOUND) })
              : t('list.failedFolder', {
                  account: accountName(failure.account_id),
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
  const { selection, select, matching } = useTriage()
  const box = useRef<HTMLInputElement>(null)
  const chosen = messages.filter((message) => selection.has(message.ref)).length
  const all = matching !== null || chosen === messages.length
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
        name="all"
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

/**
 * Stars or unstars this one message, whatever is selected or open, through the triage flag path:
 * its outcome is the toolbar's. A sibling of the row's link, so a click opens nothing. Filled when
 * starred; otherwise an outline seen on hover and focus (and always on a touch screen), but
 * always there for screen readers.
 */
function StarToggle({ message }: { message: MessageItem }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { flagsOf, run, busy } = useTriage()
  const { flagged } = flagsOf(message)
  return (
    <Button
      variant="ghost"
      size="icon-xs"
      // A toggle keeps one name; its state is aria-pressed. The tooltip says what a click does.
      aria-label={t('triage.star')}
      title={flagged ? t('triage.unstar') : t('triage.star')}
      aria-pressed={flagged}
      aria-disabled={busy}
      className={cn(
        'mt-2.5 ml-1.5 shrink-0 text-muted-foreground hover:text-foreground',
        // Not opacity: an unstarred row's outline stays hidden until hover or focus.
        'aria-disabled:cursor-not-allowed aria-disabled:text-muted-foreground/50 aria-disabled:hover:text-muted-foreground/50',
        !flagged &&
          'opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100 pointer-coarse:opacity-100',
      )}
      onClick={() => {
        if (!busy) {
          void run({ kind: 'flag', change: { flagged: !flagged } }, [message])
        }
      }}
    >
      <Star aria-hidden="true" className={cn('size-3.5', flagged && 'fill-current text-foreground')} />
    </Button>
  )
}

/**
 * Marks this one message To sort, or sorted, as `StarToggle` stars it. Greyed out, saying why, where
 * the account's server keeps no keywords.
 */
function ToSortToggle({ message }: { message: MessageItem }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { flagsOf, run, busy } = useTriage()
  const unsupported = useAccountKeywords()(message.account_id) === false
  const marked = flagsOf(message).to_sort
  const action = marked ? t('triage.sorted') : t('triage.toSort')
  return (
    <Button
      variant="ghost"
      size="icon-xs"
      aria-label={t('list.toSort')}
      title={unsupported ? `${action}: ${t('triage.toSortUnsupported')}` : action}
      aria-pressed={marked}
      aria-disabled={busy || unsupported}
      className={cn(
        'mt-2.5 shrink-0 text-muted-foreground hover:text-foreground',
        'aria-disabled:cursor-not-allowed aria-disabled:text-muted-foreground/50 aria-disabled:hover:text-muted-foreground/50',
        !marked &&
          'opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100 pointer-coarse:opacity-100',
      )}
      onClick={() => {
        if (!busy && !unsupported) {
          void run({ kind: 'flag', change: { to_sort: !marked } }, [message])
        }
      }}
    >
      <Bookmark aria-hidden="true" className={cn('size-3.5', marked && 'fill-current text-foreground')} />
    </Button>
  )
}

function MessageRow({ message, source }: { message: MessageItem; source: 'account' | 'folder' | null }): ReactElement {
  const { t, i18n } = useTranslation(MAIL_NS)
  const timeZone = useTimeZone()
  const accountLabel = useAccountLabel()
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
      aria-haspopup="menu"
      aria-keyshortcuts="Shift+F10"
      // The row drags its messages, not the link's URL.
      draggable={false}
      className={cn(
        'flex min-w-0 flex-1 flex-col gap-0.5 p-3 text-sm hover:bg-accent',
        message.ref === open && 'bg-accent',
      )}
    >
      <span className="flex items-baseline gap-2">
        <span className={cn('min-w-0 flex-1 truncate', unread && 'font-semibold')}>
          {sender === '' ? t('message.noSender') : sender}
        </span>
        {/* Shown by the row's toggles; said here too for whoever goes through the links. */}
        {flags.flagged && <span className="sr-only">{t('list.flagged')}</span>}
        {flags.to_sort && <span className="sr-only">{t('list.toSort')}</span>}
        {message.flags.answered && (
          <Reply role="img" aria-label={t('list.answered')} className="size-3.5 shrink-0 self-center text-muted-foreground" />
        )}
        {message.flags.forwarded && (
          <Forward role="img" aria-label={t('list.forwarded')} className="size-3.5 shrink-0 self-center text-muted-foreground" />
        )}
        {message.has_attachments && (
          <Paperclip role="img" aria-label={t('list.hasAttachments')} className="size-3.5 shrink-0 self-center" />
        )}
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
          {formatListDate(i18n.language, message.date, timeZone)}
        </span>
      </span>
      <span className={cn('truncate', unread ? 'font-semibold' : 'text-foreground')}>
        {unread && <span className="sr-only">{`${t('list.unread')}: `}</span>}
        {subject}
      </span>
      {message.preview !== '' && (
        <span className="line-clamp-1 text-muted-foreground">{message.preview}</span>
      )}
      {source !== null && (
        <span className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
          {source === 'account' && (
            <>
              <AccountName account={accountLabel(message.account_id)} className="max-w-1/2" />
              <span aria-hidden="true">·</span>
            </>
          )}
          <span className="truncate">{message.folder}</span>
        </span>
      )}
    </Link>
  )
}

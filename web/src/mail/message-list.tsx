import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearch } from '@tanstack/react-router'
import { defaultRangeExtractor, useVirtualizer } from '@tanstack/react-virtual'
import { Bookmark, Forward, Paperclip, Reply, Star } from 'lucide-react'

import { AccountName } from '@/accounts/account-name.tsx'
import { useAccountHandled, useAccountLabel, useAccountName, useCannotKeepHandled } from '@/accounts/accounts-context.ts'
import type { MatchScope, MessageItem, MessagePage, SourceFailure } from '@/api/mail.ts'
import { useAppearance, useTimeZone } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { Menu, type MenuItem, type MenuPoint } from '@/components/ui/menu.tsx'
import { useUnmountSignal } from '@/lib/use-unmount-signal.ts'
import { cn } from '@/lib/utils.ts'

import { ROW_ESTIMATE } from './list-size.ts'
import { measureRow } from './row-measure.ts'
import { listedActions, unavailableReason, type ActionContext } from './action-registry.ts'
import { useCompose } from './compose-context.ts'
import { isConversation, listRow, opened } from './conversations.ts'
import { useDraftFilter } from './filter-draft.ts'
import { useMuting } from './mute-context.ts'
import { useSnoozing } from './snooze-context.ts'
import { addressLabel, formatListDate } from './format.ts'
import { MAIL_NS } from './i18n.ts'
import { endMessageDrag, startMessageDrag } from './message-drag.ts'
import { ACCOUNT_NOT_FOUND, mailCodeMessage, mailErrorMessage } from './messages.ts'
import { keptRows, keptTail, withKept, type Kept, type Tail } from './list-tail.ts'
import { useLongPress } from './long-press.ts'
import { fetchPage, LIST_KEPT, messagesKey, prefetchMessage } from './queries.ts'
import type { MailSearch } from './route-search.ts'
import { SelectMatching } from './select-matching.tsx'
import { SwipeRow } from './swipe-row.tsx'
import { TOAST_RETURN_ID } from './toast.tsx'
import { useTriageActions } from './triage-actions.ts'
import { useToday } from './today.ts'
import { useTriage, type Flags, type Target } from './triage-context.ts'

type More = { kind: 'idle' } | { kind: 'loading' } | { kind: 'failed'; message: string }

/** How many rows short of the last loaded one the view may come before the next page is read. */
const READ_AHEAD = 10

/** How long the mouse rests on a row before its message is read ahead: not every row it crosses. */
const HOVER_PREFETCH_MS = 100

/**
 * A folder's messages or a search's, newest first, a page at a time, in a virtualised list (only
 * the rows in view and a few around them are in the page): the next page is read, with the
 * cursor the last page gave, as the view nears the last row loaded. Mounted anew (by key) for each
 * source; its first page is kept a while (`LIST_KEPT`), shown again at once and read again behind
 * it. Each row names where it is: its account and folder in a search, its folder in a folder's
 * subfolders. A page grouped by conversation shows each one as a row (`listRow`), which actions
 * take whole. Invalidating its first page (`messagesKey`) reads the list again, keeping it on show
 * meanwhile: new rows come in at the top, and the rows loaded below it stay, as do the rows in
 * view unless it was at the very top. Under quick filters, a row this screen changed so it no
 * longer matches stays too, until the source changes.
 */
export const MessageList = memo(function MessageList({
  source,
  loadPage,
  match,
  searching,
  filtered = false,
  subfolders = false,
  view = false,
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
  /** A view's list: its rows name their account and folder, and it is no folder. */
  view?: boolean
}): ReactElement {
  // TanStack Virtual's virtualizer changes in place, which React Compiler's memoised values would not
  // see: this component keeps its own `memo` and callbacks and is left uncompiled. Its rows are not.
  'use no memo'
  const { t } = useTranslation(MAIL_NS)
  const queryClient = useQueryClient()
  const first = useQuery({
    queryKey: messagesKey(source, null),
    queryFn: ({ signal }) => loadPage(null, signal),
    ...LIST_KEPT,
  })
  const [tail, setTail] = useState<Tail | null>(null)
  const [more, setMore] = useState<More>({ kind: 'idle' })
  const triage = useTriage()
  const { removed, setVisible, refreshed, flagsOf } = triage
  const { density } = useAppearance().appearance
  // Once a row is selected, every row's checkbox shows; before, a row's on hover or focus.
  const selecting = triage.matching !== null || triage.selection.size > 0
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
          (message) => {
            const row = listRow(message)
            return changedHere(row, flagsOf(row))
          },
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
  const hovered = useRef<number | undefined>(undefined)
  const stopHover = useCallback(() => {
    window.clearTimeout(hovered.current)
  }, [])
  useEffect(() => stopHover, [stopHover])
  const page = first.status === 'success' ? first.data : null
  // Rows moved away since are gone in place.
  const messages = (page === null ? [] : shown(page, tail)).filter((message) => !removed.has(message.ref)).map(listRow)
  useEffect(() => {
    setVisible(messages)
  })
  // Only a page read while the list is shown says the server's flags: the one kept from before may be older than the reads.
  const fetchedHere = first.isFetchedAfterMount
  useEffect(() => {
    if (page !== null && fetchedHere) {
      refreshed(source, page.messages.map(listRow))
    }
  }, [page, fetchedHere, source, refreshed])
  useEffect(
    () => () => {
      setVisible([])
    },
    [setVisible],
  )
  const nextCursor = page === null ? null : tail === null ? page.next_cursor : tail.cursor

  const scroller = useRef<HTMLDivElement>(null)
  // Uncompiled, by the 'use no memo' above.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => scroller.current,
    estimateSize: () => ROW_ESTIMATE[density],
    measureElement: measureRow,
    getItemKey: (index) => messages[index]?.ref ?? index,
    overscan: 5,
    // A row whose menu is open stays rendered, scrolled out of view too: the menu gives focus back to it.
    rangeExtractor: (range) => {
      const shown = defaultRangeExtractor(range)
      const held = menu === null ? -1 : messages.findIndex((message) => message.ref === menu.row.ref)
      return held === -1 || shown.includes(held) ? shown : [...shown, held].sort((a, b) => a - b)
    },
  })
  const rows = virtualizer.getVirtualItems()
  // The row at the top of the view stays there as rows come in or go above it.
  const anchor = useRef<{ ref: string; within: number; first: string | undefined; count: number } | null>(null)
  useLayoutEffect(() => {
    const offset = virtualizer.scrollOffset ?? 0
    const held = anchor.current
    if (held !== null && (held.first !== messages[0]?.ref || held.count !== messages.length)) {
      const item = virtualizer.measurementsCache.find((each) => each.key === held.ref)
      if (item !== undefined && item.start + held.within !== offset) {
        virtualizer.scrollToOffset(item.start + held.within)
      }
    }
    // At the very top, new mail shows.
    const top = offset > 0 ? virtualizer.getVirtualItemForOffset(offset) : undefined
    anchor.current =
      top === undefined ? null : { ref: String(top.key), within: offset - top.start, first: messages[0]?.ref, count: messages.length }
  })
  // The view nearing the last row loaded reads the next page; a failed one waits for Try again.
  const lastInView = virtualizer.range?.endIndex ?? -1
  useEffect(() => {
    if (nextCursor !== null && more.kind === 'idle' && lastInView >= messages.length - 1 - READ_AHEAD) {
      void loadMore(nextCursor)
    }
  })
  // The open row comes into view when it changes (j and k), or once the list is read.
  const current = useSearch({ from: '/mail', select: currentRow })
  const latestMessages = useRef(messages)
  useEffect(() => {
    latestMessages.current = messages
  })
  const loaded = page !== null
  useEffect(() => {
    const index = latestMessages.current.findIndex((message) => message.ref === current)
    if (index !== -1) {
      virtualizer.scrollToIndex(index, { align: 'auto' })
    }
  }, [current, loaded, virtualizer])
  // Shift+click on a checkbox sets every row from the one clicked before to this one.
  const lastChecked = useRef<string | null>(null)
  // On a touch screen, a finger held on a row selects it, as its hidden checkbox would.
  const longPress = useLongPress()

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
          {mailErrorMessage(t, first.error, t('list.loadFailed'), view ? 'views.notFound' : ACCOUNT_NOT_FOUND)}
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
  // Every loaded row selected, and more to load: the rest of what the list matches can be.
  const allLoaded = messages.length > 0 && messages.every((message) => triage.selection.has(message.ref))
  const offerMatching = match !== undefined && (triage.matching !== null || (allLoaded && nextCursor !== null))

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {failures.length > 0 && <Failures failures={failures} />}
      {/* Every loaded row moved away is not an empty folder while there is more to load. */}
      {messages.length === 0 ? (
        nextCursor === null ? (
          <p className="text-sm text-muted-foreground">
            {searching || filtered ? t('list.noMatches') : view ? t('views.empty') : t('list.empty')}
          </p>
        ) : (
          more.kind === 'loading' && (
            <p role="status" className="text-sm text-muted-foreground">
              {t('list.loadingMore')}
            </p>
          )
        )
      ) : (
        <>
          <SelectAll messages={messages} />
          {offerMatching && <SelectMatching loaded={messages.length} scope={match} />}
          {/* Its own scroll: a phone's page around it scrolls too, so it stops short of the screen. */}
          <div ref={scroller} className="max-h-[75dvh] min-h-0 overflow-y-auto rounded-lg border lg:max-h-none lg:flex-1">
            <ul
              id={TOAST_RETURN_ID}
              aria-label={searching ? t('list.results') : t('list.messages')}
              data-density={density}
              className="relative divide-y"
              style={{ height: virtualizer.getTotalSize() }}
            >
              {rows.map((row) => {
                const message = messages[row.index]
                if (message === undefined) {
                  return null
                }
                const checked = triage.matching !== null || triage.selection.has(message.ref)
                return (
                  // The row's link is what takes focus: its menu key and a right-click reach it from here.
                  // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
                  <li
                    key={row.key}
                    ref={virtualizer.measureElement}
                    data-index={row.index}
                    // Only the rows in view are in the page: each says where it stands in the list.
                    aria-posinset={row.index + 1}
                    aria-setsize={nextCursor === null ? messages.length : -1}
                    className={cn(
                      'group/row absolute top-0 left-0 w-full hover:bg-accent',
                      // The whole row, its checkbox's gutter too.
                      message.ref === current && 'bg-accent',
                      // A finger held on it selects it: no text selection or link preview of the browser's.
                      'pointer-coarse:select-none pointer-coarse:[-webkit-touch-callout:none]',
                    )}
                    style={{ transform: `translateY(${String(row.start)}px)` }}
                    // Dragged onto a folder, it moves the selection when it is in it (as every row is while
                    // every matching message is selected), else itself alone.
                    draggable
                    onDragStart={(event) => {
                      const inSelection = triage.matching !== null || triage.selection.has(message.ref)
                      const targets = inSelection ? [...triage.selection.values()] : [message]
                      startMessageDrag(event.dataTransfer, targets, t('list.dragging', { count: targets.length }))
                    }}
                    onDragEnd={endMessageDrag}
                    // Its message is read ahead while the mouse rests on it, or as soon as it is pressed.
                    onPointerEnter={(event) => {
                      if (event.pointerType === 'mouse') {
                        stopHover()
                        hovered.current = window.setTimeout(() => {
                          prefetchMessage(queryClient, message.ref)
                        }, HOVER_PREFETCH_MS)
                      }
                    }}
                    onPointerLeave={stopHover}
                    onPointerDownCapture={() => {
                      prefetchMessage(queryClient, message.ref)
                    }}
                    {...longPress.bind(() => {
                      lastChecked.current = message.ref
                      triage.select([message], !checked)
                    })}
                    onContextMenu={(event) => {
                      // A finger held on the row selects it rather than opening its menu.
                      if (longPress.held()) {
                        event.preventDefault()
                        return
                      }
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
                    <SwipeRow message={message}>
                      <input
                        type="checkbox"
                        name="message"
                        value={message.ref}
                        aria-label={t('triage.select', { subject: message.subject === '' ? t('message.noSubject') : message.subject })}
                        className={cn(
                          'mr-0 ml-3 size-4 shrink-0 accent-primary',
                          density === 'compact' ? 'my-2' : 'my-3',
                          // Hidden, not removed: it keeps its place and the keyboard still reaches it. On a touch
                          // screen it takes no width either until a row is selected (a finger held on one).
                          !selecting &&
                            'opacity-0 group-focus-within/row:opacity-100 group-hover/row:opacity-100 pointer-coarse:sr-only',
                        )}
                        checked={checked}
                        onChange={(event) => {
                          const from = event.nativeEvent instanceof MouseEvent && event.nativeEvent.shiftKey
                            ? messages.findIndex((each) => each.ref === lastChecked.current)
                            : -1
                          lastChecked.current = message.ref
                          triage.select(
                            from === -1 ? [message] : messages.slice(Math.min(from, row.index), Math.max(from, row.index) + 1),
                            event.target.checked,
                          )
                        }}
                      />
                      <MessageRow
                        message={message}
                        source={searching || view ? 'account' : subfolders ? 'folder' : null}
                        compact={density === 'compact'}
                      />
                      {/* Over the row's end, taking no width, while the row is hovered or has focus; not on a
                          touch screen, where the message's actions and a swipe star or mark it instead. */}
                      <span
                        className={cn(
                          'absolute right-2 flex items-center gap-0.5 rounded-md border bg-background shadow-xs',
                          density === 'compact' ? 'top-1' : 'top-2',
                          // Hidden, it takes no click: a tap on the date opens the message.
                          'pointer-events-none opacity-0 group-focus-within/row:pointer-events-auto group-focus-within/row:opacity-100',
                          'group-hover/row:pointer-events-auto group-hover/row:opacity-100',
                          // Still there for screen readers.
                          'pointer-coarse:sr-only',
                        )}
                      >
                        <StarToggle message={message} />
                        <HandledToggle message={message} />
                      </span>
                    </SwipeRow>
                  </li>
                )
              })}
            </ul>
            {more.kind === 'loading' && (
              <p role="status" className="p-3 text-center text-sm text-muted-foreground">
                {t('list.loadingMore')}
              </p>
            )}
          </div>
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
      {more.kind === 'failed' && nextCursor !== null && (
        <div className="flex items-center gap-3">
          <p role="alert" className="text-sm text-destructive">
            {more.message}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void loadMore(nextCursor)
            }}
          >
            {t('auth.retry')}
          </Button>
        </div>
      )}
    </div>
  )
})

/** The list's current row: the open conversation's, else the open message's. */
function currentRow({ message, thread }: MailSearch): string | undefined {
  return thread ?? message
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
  const snoozing = useSnoozing()
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
    snoozing,
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
  return flags.seen !== message.flags.seen || flags.flagged !== message.flags.flagged || flags.handled !== message.flags.handled
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
 * its outcome is the toolbar's. A sibling of the row's link, so a click opens nothing. Shown over
 * the row on hover and focus (not on a touch screen), always there for screen readers; a starred
 * row says so by its mark.
 */
function StarToggle({ message }: { message: MessageItem }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { flagsOf, run, busy } = useTriage()
  const { flagged } = flagsOf(message)
  const handled = useAccountHandled()(message.account_id).kind === 'star'
  const action = flagged ? t('triage.unstar') : t('triage.star')
  return (
    <Button
      variant="ghost"
      size="icon-xs"
      // A toggle keeps one name; its state is aria-pressed. The tooltip says what a click does.
      aria-label={t('triage.star')}
      title={handled ? t('triage.starIsHandled', { action }) : action}
      aria-pressed={flagged}
      aria-disabled={busy}
      className={cn(
        'shrink-0 text-muted-foreground hover:text-foreground',
        'aria-disabled:cursor-not-allowed aria-disabled:text-muted-foreground/50 aria-disabled:hover:text-muted-foreground/50',
      )}
      onClick={() => {
        if (!busy) {
          void run({ kind: 'flag', change: { flagged: !flagged } }, [message])
        }
      }}
    >
      <Star aria-hidden="true" className={cn('size-3.5', flagged && 'fill-current text-star')} />
    </Button>
  )
}

/**
 * Marks this one message Handled, or clears the mark, as `StarToggle` stars it. Greyed out, saying
 * why, where the account's server cannot keep it; not there where Handled is the star.
 */
function HandledToggle({ message }: { message: MessageItem }): ReactElement | null {
  const { t } = useTranslation(MAIL_NS)
  const { flagsOf, run, busy } = useTriage()
  const unsupported = useCannotKeepHandled()(message.account_id)
  const star = useAccountHandled()(message.account_id).kind === 'star'
  if (star) {
    return null
  }
  const marked = flagsOf(message).handled
  const action = marked ? t('triage.unmarkHandled') : t('triage.markHandled')
  return (
    <Button
      variant="ghost"
      size="icon-xs"
      aria-label={t('list.handled')}
      title={unsupported ? `${action}: ${t('triage.handledUnsupported')}` : action}
      aria-pressed={marked}
      aria-disabled={busy || unsupported}
      className={cn(
        'shrink-0 text-muted-foreground hover:text-foreground',
        'aria-disabled:cursor-not-allowed aria-disabled:text-muted-foreground/50 aria-disabled:hover:text-muted-foreground/50',
      )}
      onClick={() => {
        if (!busy && !unsupported) {
          void run({ kind: 'flag', change: { handled: !marked } }, [message])
        }
      }}
    >
      <Bookmark aria-hidden="true" className={cn('size-3.5', marked && 'fill-current text-primary')} />
    </Button>
  )
}

/**
 * A message's row, or a conversation's: its first writers, how many messages it has, and its
 * latest message's subject, date and opening (not in a compact row). A star and a Handled mark
 * by the date say the row's flags, which its toggles change.
 */
function MessageRow({
  message,
  source,
  compact,
}: {
  message: MessageItem
  source: 'account' | 'folder' | null
  compact: boolean
}): ReactElement {
  const { t, i18n } = useTranslation(MAIL_NS)
  const handledMark = useAccountHandled()(message.account_id)
  const timeZone = useTimeZone()
  const today = useToday(timeZone)
  const accountLabel = useAccountLabel()
  // A conversation stays the current row while another of its messages is read. Only whether it
  // is: opening a message draws its row and the one before again, not every row.
  const current = useSearch({ from: '/mail', select: (search) => message.ref === currentRow(search) })
  const flags = useTriage().flagsOf(message)
  const unread = !flags.seen
  const subject = message.subject === '' ? t('message.noSubject') : message.subject
  const conversation = isConversation(message) ? message.conversation : undefined
  const sender = conversation === undefined ? addressLabel(message.from) : conversation.participants.join(', ')
  return (
    <Link
      to="/mail"
      search={(previous) => ({ ...previous, ...opened(message) })}
      aria-current={current ? 'true' : undefined}
      data-ref={message.ref}
      aria-haspopup="menu"
      aria-keyshortcuts="Shift+F10"
      // The row drags its messages, not the link's URL.
      draggable={false}
      className={cn(
        'flex min-w-0 flex-1 flex-col gap-0.5 text-sm',
        compact ? 'px-3 py-1.5' : 'p-3',
      )}
    >
      <span className="flex items-baseline gap-2">
        <span className={cn('min-w-0 flex-1 truncate', unread && 'font-semibold')}>
          {sender === '' ? t('message.noSender') : sender}
        </span>
        {conversation !== undefined && (
          <span className="shrink-0 rounded-full bg-muted px-1.5 text-xs text-muted-foreground tabular-nums">
            <span aria-hidden="true">{conversation.count}</span>
            <span className="sr-only">{t('list.conversation', { count: conversation.count })}</span>
          </span>
        )}
        {message.flags.answered && (
          <Reply role="img" aria-label={t('list.answered')} className="size-3.5 shrink-0 self-center text-muted-foreground" />
        )}
        {message.flags.forwarded && (
          <Forward role="img" aria-label={t('list.forwarded')} className="size-3.5 shrink-0 self-center text-muted-foreground" />
        )}
        {(conversation?.has_attachments ?? message.has_attachments) && (
          <Paperclip role="img" aria-label={t('list.hasAttachments')} className="size-3.5 shrink-0 self-center" />
        )}
        {flags.handled && handledMark.kind !== 'star' && (
          <Bookmark role="img" aria-label={t('list.handled')} className="size-3 shrink-0 self-center fill-current text-primary" />
        )}
        {flags.flagged && (
          <Star role="img" aria-label={t('list.flagged')} className="size-3 shrink-0 self-center fill-current text-star" />
        )}
        {/* Under the row's toggles while they show: none of it shows past their edge, though a
            screen reader still reads it. */}
        <span
          className={cn(
            'shrink-0 text-xs text-muted-foreground tabular-nums',
            'group-hover/row:text-transparent group-focus-within/row:text-transparent pointer-coarse:text-muted-foreground',
          )}
        >
          {formatListDate(i18n.language, message.date, timeZone, today)}
        </span>
      </span>
      <span className={cn('truncate', unread ? 'font-semibold' : 'text-foreground')}>
        {unread && <span className="sr-only">{`${t('list.unread')}: `}</span>}
        {subject}
      </span>
      {!compact && message.preview !== '' && (
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

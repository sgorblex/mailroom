import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'

import { LIVE_EVENT_TYPES, mailEventsUrl, parseLiveEvent, type IndexStatus, type LiveEvent, type MailFolder } from '@/api/mail.ts'

import { DesktopNotifications } from './desktop-notifications.tsx'
import { LiveUpdatesContext, type LiveStatus, type LiveUpdates, type Viewing } from './live-context.ts'
import { liveCountReach } from './live-counts.ts'
import { ALL_FOLDERS_KEY, ALL_MESSAGES_KEY, foldersKey, INDEX_KEY, indexStatusKey } from './queries.ts'
import { isSameFolder } from './triage-context.ts'

/** The wait before the first reconnect, doubled after each failure up to `MAX_BACKOFF_MS`. */
const FIRST_BACKOFF_MS = 1_000
const MAX_BACKOFF_MS = 60_000

/** What every event does to the cache, whichever screen is open: counts and index statuses. */
function applyEvent(queryClient: QueryClient, event: LiveEvent): void {
  if (event.type === 'folder_changed') {
    const folders = queryClient.getQueryData<MailFolder[]>(foldersKey(event.account))
    const reach = liveCountReach(folders, event)
    if (folders === undefined || reach === null) {
      return
    }
    if (reach.kind === 'account') {
      // On Gmail INBOX and the labels read from All Mail: their counts are not in the event.
      void queryClient.invalidateQueries({ queryKey: foldersKey(event.account) })
      return
    }
    queryClient.setQueryData<MailFolder[]>(
      foldersKey(event.account),
      folders.map((folder) => (folder === reach.folder ? { ...folder, unread: event.unread, total: event.total } : folder)),
    )
  } else if (event.type === 'sync') {
    queryClient.setQueriesData<IndexStatus>({ queryKey: indexStatusKey(event.account) }, (status) =>
      status === undefined
        ? status
        : {
            ...status,
            folders: status.folders.map((folder) =>
              folder.indexed_as === 'folder' && isSameFolder(folder.path, event.folder)
                ? { ...folder, state: event.state, indexed: event.progress }
                : folder,
            ),
          },
    )
  }
}

/**
 * The signed-in user's live updates stream: one EventSource to `/mail/events`, opened again
 * with the folder the mail screen views when it changes, and after a failure with a backoff.
 * Events are not replayed, so what the screen shows is read again once it reconnects, and the
 * folders' counts once it is opened again for another folder, but for those read since it closed. New mail shows as desktop
 * notifications (`DesktopNotifications`).
 */
export function LiveUpdatesProvider({ children }: { children: ReactNode }): ReactElement {
  const queryClient = useQueryClient()
  const [viewing, setViewingState] = useState<Viewing | null>(null)
  const [status, setStatus] = useState<LiveStatus>(() => (typeof EventSource === 'undefined' ? 'off' : 'connecting'))
  const listeners = useRef(new Set<(event: LiveEvent) => void>())
  const failures = useRef(0)
  const openedBefore = useRef(false)
  // When the last stream closed: what was read since saw what it missed.
  const closedAt = useRef(0)

  const setViewing = useCallback((next: Viewing | null) => {
    setViewingState((current) =>
      current?.account === next?.account && current?.folder === next?.folder ? current : next,
    )
  }, [])
  const subscribe = useCallback((listener: (event: LiveEvent) => void) => {
    listeners.current.add(listener)
    return () => {
      listeners.current.delete(listener)
    }
  }, [])

  useEffect(() => {
    if (typeof EventSource === 'undefined') {
      return
    }
    let source: EventSource | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    const open = (): void => {
      const stream = new EventSource(mailEventsUrl(viewing), { withCredentials: true })
      source = stream
      stream.addEventListener('open', () => {
        if (failures.current > 0) {
          void queryClient.invalidateQueries({ queryKey: ALL_FOLDERS_KEY })
          void queryClient.invalidateQueries({ queryKey: ALL_MESSAGES_KEY })
          void queryClient.invalidateQueries({ queryKey: INDEX_KEY })
        } else if (openedBefore.current) {
          // Opened again for another folder: what changed while it was closed moved the counts,
          // unless they were read since (the sidebar reads them as it mounts).
          const since = closedAt.current
          void queryClient.invalidateQueries({
            queryKey: ALL_FOLDERS_KEY,
            predicate: (query) => query.state.fetchStatus === 'idle' && query.state.dataUpdatedAt < since,
          })
        }
        failures.current = 0
        openedBefore.current = true
        setStatus('open')
      })
      // The browser's own reconnect does not back off, nor follows a refusal: this one does both.
      stream.addEventListener('error', () => {
        stream.close()
        const wait = Math.min(FIRST_BACKOFF_MS * 2 ** failures.current, MAX_BACKOFF_MS)
        failures.current += 1
        setStatus('reconnecting')
        retry = setTimeout(open, wait)
      })
      for (const type of LIVE_EVENT_TYPES) {
        stream.addEventListener(type, (message) => {
          const event = parseLiveEvent(type, (message as MessageEvent<string>).data)
          if (event === null) {
            return
          }
          applyEvent(queryClient, event)
          for (const listener of listeners.current) {
            listener(event)
          }
        })
      }
    }
    open()
    return () => {
      clearTimeout(retry)
      source?.close()
      closedAt.current = Date.now()
    }
  }, [viewing, queryClient])

  const value = useMemo<LiveUpdates>(() => ({ status, setViewing, subscribe }), [status, setViewing, subscribe])
  return (
    <LiveUpdatesContext value={value}>
      <DesktopNotifications viewing={viewing} />
      {children}
    </LiveUpdatesContext>
  )
}

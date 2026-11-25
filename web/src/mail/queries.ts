import { useQueries, type QueryClient, type UseQueryOptions } from '@tanstack/react-query'

import type { AccountId } from '@/api/accounts.ts'
import {
  listMailFolders,
  listViews,
  readMessage,
  readThread,
  type FlagChange,
  type MailFolder,
  type MessagePage,
  type MessageView,
  type SavedView,
} from '@/api/mail.ts'

/** The keys the mail screen's reads are cached under. */
export const ACCOUNTS_KEY = ['accounts'] as const

/** Every account's folders. */
export const ALL_FOLDERS_KEY = ['folders'] as const

export function foldersKey(account: AccountId): readonly unknown[] {
  return [...ALL_FOLDERS_KEY, account]
}

/** Every page of every list. */
export const ALL_MESSAGES_KEY = ['messages'] as const

/** A page of the list `source` names (a folder or a search, as the mail screen's `sourceKey`). */
export function messagesKey(source: string, cursor: string | null): readonly unknown[] {
  return ['messages', source, cursor]
}

/** The user's held sends: the Scheduled list. */
export const SCHEDULED_KEY = ['mail-scheduled'] as const

/** The user's snoozed messages: the Snoozed list. */
export const SNOOZES_KEY = ['mail-snoozes'] as const

/** The user's saved views. */
export const VIEWS_KEY = ['mail-views'] as const

/** Every account's mail index status. */
export const INDEX_KEY = ['mail-index'] as const

export function indexStatusKey(account: AccountId): readonly unknown[] {
  return [...INDEX_KEY, account]
}

/** The account's index status as the indexing line reads it: the waiting folders' counts only. */
export function indexWaitingKey(account: AccountId): readonly unknown[] {
  return [...indexStatusKey(account), 'waiting']
}

/** The account's identities with their signatures. */
export function signaturesKey(account: AccountId): readonly unknown[] {
  return ['signatures', account]
}

/** The account's vacation reply. */
export function vacationKey(account: AccountId): readonly unknown[] {
  return ['vacation', account]
}

/** The addresses a recipient field suggests for what was typed. */
export function addressesKey(prefix: string): readonly unknown[] {
  return ['mail-addresses', prefix]
}

/** Each account's folders whose new mail shows as a desktop notification. */
export const NOTIFICATIONS_KEY = ['mail-notifications'] as const

/** How often an index status is read again while a folder's first sync is under way. */
export const INDEXING_POLL_MS = 15_000

/** Every open conversation's messages: under the lists' key, so what reads the lists again reads them too. */
export const ALL_THREADS_KEY = [...ALL_MESSAGES_KEY, 'thread'] as const

/** The conversation of the message `ref` names, read live. */
export function threadKey(ref: string): readonly unknown[] {
  return [...ALL_THREADS_KEY, ref]
}

/** Every read of every message. */
export const ALL_READS_KEY = ['message'] as const

export function messageKey(ref: string, offset: number, includeQuoted: boolean): readonly unknown[] {
  return [...ALL_READS_KEY, ref, offset, includeQuoted]
}

/**
 * Read again each time it is shown, as before the cache: the screen's own flag changes and the rows
 * it moved away are not written into it.
 */
export const READ_ON_MOUNT = { gcTime: 0 } as const

/** A message as opened, with the choice it was read with. */
export interface OpenedMessage {
  view: MessageView
  includeQuoted: boolean
}

/**
 * How long a message read is kept once nothing shows it, so going back to it, or opening the one
 * hovered or next in the list, shows it at once; read again in the background when older than the
 * default `staleTime` (`lib/query-client.ts`). The screen's own flag changes and moves are written
 * into it (`setCachedReadFlags`, `forgetCachedReads`).
 */
const READ_GC_MS = 5 * 60_000

/** How old a kept read saying its sender is trusted may be and still be shown without reading it again. */
const TRUST_READ_MS = 5_000

/**
 * A list's first page (`messagesKey(source, null)`): kept as long as the reads, so coming back to
 * the list (from Home, from another folder) shows it at once, and read again behind it each time
 * it is shown. Triage and live events invalidate it as before; a kept page is never taken for the
 * server's word on flags (`MessageList`'s `refreshed`).
 */
export const LIST_KEPT = {
  gcTime: READ_GC_MS,
  refetchOnMount: (query: ListQuery) => (justReadAhead(query) ? false : 'always'),
} as const

/** How long a first page read ahead (`readListAhead`) is shown as it is when its list comes up. */
const AHEAD_MS = 10_000

/** Each first page read ahead, with when that read landed. */
const readAhead = new WeakMap<object, number>()

/** What `LIST_KEPT` reads of a list's cached first page. */
interface ListQuery {
  state: { dataUpdatedAt: number; isInvalidated: boolean }
}

/** Read ahead lately, and neither read again, written nor invalidated since. */
function justReadAhead(query: ListQuery): boolean {
  const { dataUpdatedAt, isInvalidated } = query.state
  return readAhead.get(query) === dataUpdatedAt && !isInvalidated && Date.now() - dataUpdatedAt < AHEAD_MS
}

/**
 * Reads the first page of the list `source` names ahead of the list showing it, as the mail
 * screen does while its accounts load: the list, coming up just after, shows it without reading
 * it again.
 */
export async function readListAhead(
  queryClient: QueryClient,
  source: string,
  read: (signal: AbortSignal) => Promise<unknown>,
): Promise<void> {
  const queryKey = messagesKey(source, null)
  // A failure is the list's to show, once it reads the page itself.
  await queryClient.query({ queryKey, queryFn: ({ signal }) => read(signal), gcTime: READ_GC_MS }).catch(() => undefined)
  const query = queryClient.getQueryCache().find({ queryKey, exact: true })
  if (query?.state.status === 'success') {
    readAhead.set(query, query.state.dataUpdatedAt)
  }
}

/**
 * The message `ref` from its start, as the message view opens it and a prefetch reads it ahead.
 * A read once asked always lands in the cache: it takes no abort signal, which would let the last
 * view leaving it (a remount, the reader moving on) cancel it and have the next view ask again.
 */
export function messageQuery(ref: string, includeQuoted: boolean): UseQueryOptions<OpenedMessage, Error, OpenedMessage> {
  return {
    queryKey: messageKey(ref, 0, includeQuoted),
    queryFn: async () => ({ view: await readMessage(ref, { includeQuoted }), includeQuoted }),
    gcTime: READ_GC_MS,
    // Its remote content follows the trust the server finds as it serves the document
    // (`RemoteChoice` 'trusted'); what the screen says of it is read again behind it.
    refetchOnMount: (query) =>
      query.state.data?.view.remote_trusted === true && Date.now() - query.state.dataUpdatedAt > TRUST_READ_MS
        ? 'always'
        : true,
  }
}

/**
 * Reads the message `ref` ahead (the row hovered or pressed, the next one of j/k), unless a fresh
 * read is cached. Reading never marks it read: only showing it does.
 */
export function prefetchMessage(queryClient: QueryClient, ref: string): void {
  // A failure is the open view's to show, should the message be opened.
  void queryClient.query(messageQuery(ref, false)).catch(() => undefined)
}

/**
 * Writes flags into the cached reads (`messageQuery`): for each message, what `change` answers,
 * nothing where it answers `undefined`. So an action's flags show when the message opens again.
 */
export function setCachedReadFlags(
  queryClient: QueryClient,
  change: (view: MessageView) => FlagChange | undefined,
): void {
  for (const query of queryClient.getQueryCache().findAll({ queryKey: ALL_READS_KEY })) {
    const data = query.state.data as OpenedMessage | undefined
    const flags = query.queryKey[2] === 0 && data !== undefined ? change(data.view) : undefined
    if (data !== undefined && flags !== undefined) {
      queryClient.setQueryData<OpenedMessage>(query.queryKey, {
        ...data,
        view: { ...data.view, flags: { ...data.view.flags, ...flags } },
      })
    }
  }
}

/**
 * What the server remembers of the sender of the message `ref` changed (its remote images shown,
 * the view it opens in): that message's cached reads take `fields`, and the sender's other
 * messages are read again when they open.
 */
export function senderChanged(
  queryClient: QueryClient,
  ref: string,
  address: string | undefined,
  fields: Partial<Pick<MessageView, 'remote_trusted' | 'preferred_view'>>,
): void {
  const sender = address?.toLowerCase()
  const fromSender = (view: MessageView): boolean =>
    sender !== undefined && view.ref !== ref && view.from?.address.toLowerCase() === sender
  for (const query of queryClient.getQueryCache().findAll({ queryKey: ALL_READS_KEY })) {
    const data = query.state.data as OpenedMessage | undefined
    if (query.queryKey[2] === 0 && data?.view.ref === ref) {
      queryClient.setQueryData<OpenedMessage>(query.queryKey, { ...data, view: { ...data.view, ...fields } })
    }
  }
  queryClient.removeQueries({
    queryKey: ALL_READS_KEY,
    type: 'inactive',
    predicate: (query) => {
      const data = query.state.data as OpenedMessage | undefined
      return data?.view !== undefined && fromSender(data.view)
    },
  })
}

/** Drops the cached reads of messages moved away: their refs now name nothing, or another message. */
export function forgetCachedReads(queryClient: QueryClient, refs: ReadonlySet<string>): void {
  const picks = (key: readonly unknown[]): boolean => key[0] === ALL_READS_KEY[0] && refs.has(key[1] as string)
  // The one still shown (its pane closing) is only marked stale: dropping it under its view would read it again.
  void queryClient.invalidateQueries({ predicate: (query) => picks(query.queryKey), refetchType: 'none' })
  queryClient.removeQueries({ predicate: (query) => picks(query.queryKey), type: 'inactive' })
}

/** The conversation of the message `ref` names, read again each time it is shown. */
export function threadQuery(ref: string): UseQueryOptions<MessagePage, Error, MessagePage> {
  return { queryKey: threadKey(ref), queryFn: ({ signal }) => readThread(ref, signal), ...READ_ON_MOUNT }
}

/** An account's folders: read again only when a caller asks (`invalidateQueries`, or `refetchOnMount`). */
export function foldersQuery(account: AccountId): UseQueryOptions<MailFolder[], Error, MailFolder[]> {
  return {
    queryKey: foldersKey(account),
    queryFn: ({ signal }) => listMailFolders(account, signal),
    refetchOnMount: false,
    retryOnMount: false,
  }
}

/** The user's saved views, in their order. */
export function viewsQuery(): UseQueryOptions<SavedView[], Error, SavedView[]> {
  return { queryKey: VIEWS_KEY, queryFn: ({ signal }) => listViews(signal) }
}

/** A page read through the cache on request (the list scrolled near its end), dropped once `signal` aborts (an unmount). */
export function fetchPage<T>(
  queryClient: QueryClient,
  queryKey: readonly unknown[],
  read: (signal: AbortSignal) => Promise<T>,
  signal: AbortSignal,
): Promise<T> {
  signal.addEventListener(
    'abort',
    () => {
      void queryClient.cancelQueries({ queryKey })
    },
    { once: true },
  )
  return queryClient.query({ queryKey, queryFn: ({ signal: querySignal }) => read(querySignal), staleTime: 0, ...READ_ON_MOUNT })
}

/** Each account's folders as already listed, read from the cache only: nothing is asked for them here. */
export function useListedFolders(accounts: readonly AccountId[]): (account: AccountId) => MailFolder[] | undefined {
  const lists = useQueries({
    queries: accounts.map((account) => ({ ...foldersQuery(account), enabled: false })),
  })
  return (account) => lists[accounts.indexOf(account)]?.data
}

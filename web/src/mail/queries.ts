import type { QueryClient, UseQueryOptions } from '@tanstack/react-query'

import type { AccountId } from '@/api/accounts.ts'
import { listMailFolders, listViews, type MailFolder, type SavedView } from '@/api/mail.ts'

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

export function messageKey(ref: string, offset: number, includeQuoted: boolean): readonly unknown[] {
  return ['message', ref, offset, includeQuoted]
}

/**
 * Read again each time it is shown, as before the cache: the screen's own flag changes and the rows
 * it moved away are not written into it.
 */
export const READ_ON_MOUNT = { gcTime: 0 } as const

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

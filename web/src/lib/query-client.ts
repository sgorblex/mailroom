import { QueryClient } from '@tanstack/react-query'

/** The browser's cache of what the API answered: in memory only, never persisted. */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false, staleTime: 30_000 },
    },
  })
}

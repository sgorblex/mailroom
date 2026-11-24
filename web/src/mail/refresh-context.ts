import { createContext, useContext } from 'react'

export interface Refresh {
  /** The list shown is being refreshed. */
  busy: boolean
  /** Syncs what the list shows, then reads it again; nothing while one is under way. */
  refresh: () => void
}

export const RefreshContext = createContext<Refresh>({ busy: false, refresh: () => undefined })

export function useRefresh(): Refresh {
  return useContext(RefreshContext)
}

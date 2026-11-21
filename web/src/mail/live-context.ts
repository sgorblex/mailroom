import { createContext, useContext, useEffect, useRef } from 'react'

import type { AccountId } from '@/api/accounts.ts'
import type { LiveEvent } from '@/api/mail.ts'

/** The folder a browser shows: the live updates stream holds its viewing interest. */
export interface Viewing {
  account: AccountId
  folder: string
}

/** `connecting` until the stream first opens; `reconnecting` from a failure until it opens again. */
export type LiveStatus = 'off' | 'connecting' | 'open' | 'reconnecting'

export interface LiveUpdates {
  status: LiveStatus
  setViewing: (viewing: Viewing | null) => void
  /** Calls `listener` with each event; the returned function stops it. */
  subscribe: (listener: (event: LiveEvent) => void) => () => void
}

const OFF: LiveUpdates = {
  status: 'off',
  setViewing: () => undefined,
  subscribe: () => () => undefined,
}

export const LiveUpdatesContext = createContext<LiveUpdates>(OFF)

export function useLiveStatus(): LiveStatus {
  return useContext(LiveUpdatesContext).status
}

/** The stream holds `viewing`'s interest while the caller is mounted. */
export function useLiveViewing(viewing: Viewing | null): void {
  const { setViewing } = useContext(LiveUpdatesContext)
  const account = viewing?.account
  const folder = viewing?.folder
  useEffect(() => {
    setViewing(account === undefined || folder === undefined ? null : { account, folder })
  }, [setViewing, account, folder])
  useEffect(
    () => () => {
      setViewing(null)
    },
    [setViewing],
  )
}

/** Calls `listener` (its latest version) with each live event while the caller is mounted. */
export function useLiveEvents(listener: (event: LiveEvent) => void): void {
  const { subscribe } = useContext(LiveUpdatesContext)
  const latest = useRef(listener)
  useEffect(() => {
    latest.current = listener
  })
  useEffect(
    () =>
      subscribe((event) => {
        latest.current(event)
      }),
    [subscribe],
  )
}

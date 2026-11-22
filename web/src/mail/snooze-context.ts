import { createContext, useContext } from 'react'

import type { Target } from './triage-context.ts'

/** Snoozing a message: it waits in its account's Snoozed folder until a time the user picks. */
export interface Snoozing {
  /** Asks when, then snoozes the message. */
  snooze: (message: Pick<Target, 'ref' | 'account_id'> & Partial<Pick<Target, 'listed_folder'>>) => void
}

export const SnoozeContext = createContext<Snoozing | null>(null)

/** Snoozing, inside the mail screen; else `undefined`. */
export function useSnoozing(): Snoozing | undefined {
  return useContext(SnoozeContext) ?? undefined
}

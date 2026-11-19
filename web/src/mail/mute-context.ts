import { createContext, useContext } from 'react'

import type { MutedThread } from '@/api/mail.ts'

import { useFiltersOffered } from './filter-draft.ts'
import type { Target } from './triage-context.ts'

/**
 * Muting the conversation of a message, and unmuting it again. Which messages' conversations are
 * muted is known only for those muted on this screen: the API answers no subject and no id.
 */
export interface Muting {
  /** The muted thread the message's conversation was muted as here, else `undefined`. */
  mutedAs: (ref: string) => MutedThread | undefined
  mute: (message: Pick<Target, 'ref' | 'account_id'>) => void
  unmute: (message: Pick<Target, 'ref'>) => void
}

export const MuteContext = createContext<Muting | null>(null)

/** Muting, where the instance runs filters (a muted thread is a hidden filter's); else `undefined`. */
export function useMuting(): Muting | undefined {
  const muting = useContext(MuteContext)
  return useFiltersOffered() && muting !== null ? muting : undefined
}

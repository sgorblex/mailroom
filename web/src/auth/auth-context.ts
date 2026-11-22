import { createContext, useContext, useMemo } from 'react'

import type { Feature, User } from '@/api/auth.ts'
import { writableTimeZone } from '@/lib/time-zone.ts'

export type AuthState =
  | { status: 'checking' }
  /** `ended`: the session ended under the user, so the sign-in screen says why. */
  | { status: 'signed-out'; ended?: boolean }
  | { status: 'unreachable' }
  | { status: 'signed-in'; user: User }

export type AuthContextValue = AuthState & {
  /** The instance's features, the same whoever is signed in; none until the API has answered. */
  features: ReadonlySet<Feature>
  /** Resolves once signed in; rejects with the API error otherwise. */
  signIn: (username: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  /** Saves the signed-in user's time zone (`null` unsets it); rejects with the API error. */
  changeTimeZone: (timeZone: string | null) => Promise<void>
  /** Saves what "Mark all as read" on a folder with subfolders does first; rejects with the API error. */
  changeMarkReadSubfolders: (subfolders: boolean) => Promise<void>
  /** Saves the user's own age limit of the mail index (`null`: none); rejects with the API error. */
  changeIndexMaxAge: (days: number | null) => Promise<void>
  /** Saves whether the message lists are grouped by conversation; rejects with the API error. */
  changeConversations: (conversations: boolean) => Promise<void>
  /** Saves how long Send holds a message for an Undo (0: none); rejects with the API error. */
  changeUndoSend: (seconds: number) => Promise<void>
  /** Ask the API again, after it could not be reached. */
  recheck: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (value === null) {
    throw new Error('useAuth must be used inside AuthProvider')
  }
  return value
}

/** Whether the instance runs `feature`. */
export function useFeature(feature: Feature): boolean {
  return useAuth().features.has(feature)
}

/** Whether the instance reads and sends mail at all: the mail client or MCP. */
export function useMailLayer(): boolean {
  const { features } = useAuth()
  return features.has('mail') || features.has('mcp')
}

/**
 * Whether "Mark all as read" on a folder with subfolders marks them too, unless the user picks the
 * other item: the signed-in user's choice, else (outside an `AuthProvider` too) `true`.
 */
export function useMarkReadSubfolders(): boolean {
  const auth = useContext(AuthContext)
  return auth?.status === 'signed-in' ? auth.user.mark_read_subfolders : true
}

/**
 * Whether the message lists are grouped by conversation: the signed-in user's choice, else
 * (outside an `AuthProvider`) `false`.
 */
export function useConversations(): boolean {
  const auth = useContext(AuthContext)
  return auth?.status === 'signed-in' ? auth.user.conversations : false
}

/**
 * How long Send holds a message for an Undo, in seconds: the signed-in user's choice, else
 * (outside an `AuthProvider`) 0, which sends at once.
 */
export function useUndoSendSeconds(): number {
  const auth = useContext(AuthContext)
  return auth?.status === 'signed-in' ? auth.user.undo_send_seconds : 0
}

/**
 * The zone dates are written in: the signed-in user's, else (none saved, or one this browser does
 * not know) `undefined`, which `Intl` reads as the browser's. Outside an `AuthProvider` too.
 */
export function useTimeZone(): string | undefined {
  const auth = useContext(AuthContext)
  const saved = auth?.status === 'signed-in' ? auth.user.time_zone : null
  return useMemo(() => writableTimeZone(saved), [saved])
}

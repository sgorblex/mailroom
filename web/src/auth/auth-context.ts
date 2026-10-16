import { createContext, useContext } from 'react'

import type { User } from '@/api/auth.ts'

export type AuthState =
  | { status: 'checking' }
  | { status: 'signed-out' }
  | { status: 'unreachable' }
  | { status: 'signed-in'; user: User }

export type AuthContextValue = AuthState & {
  /** Resolves once signed in; rejects with the API error otherwise. */
  signIn: (username: string, password: string) => Promise<void>
  signOut: () => Promise<void>
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

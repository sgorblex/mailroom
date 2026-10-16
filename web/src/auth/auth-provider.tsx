import { useCallback, useEffect, useMemo, useState, type ReactElement, type ReactNode } from 'react'

import { fetchCurrentUser, login, logout } from '@/api/auth.ts'
import { AuthContext, type AuthContextValue, type AuthState } from '@/auth/auth-context.ts'

/** Asks the API once who this browser is signed in as, then tracks sign-in and sign-out. */
export function AuthProvider({ children }: { children: ReactNode }): ReactElement {
  const [state, setState] = useState<AuthState>({ status: 'checking' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    fetchCurrentUser(controller.signal).then(
      (user) => {
        setState(user === null ? { status: 'signed-out' } : { status: 'signed-in', user })
      },
      () => {
        if (!controller.signal.aborted) {
          setState({ status: 'unreachable' })
        }
      },
    )
    return () => {
      controller.abort()
    }
  }, [attempt])

  const signIn = useCallback(async (username: string, password: string) => {
    const user = await login(username, password)
    setState({ status: 'signed-in', user })
  }, [])

  const signOut = useCallback(async () => {
    await logout()
    setState({ status: 'signed-out' })
  }, [])

  const recheck = useCallback(() => {
    setState({ status: 'checking' })
    setAttempt((value) => value + 1)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({ ...state, signIn, signOut, recheck }),
    [state, signIn, signOut, recheck],
  )
  return <AuthContext value={value}>{children}</AuthContext>
}

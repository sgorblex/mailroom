import { useCallback, useEffect, useMemo, useState, type ReactElement, type ReactNode } from 'react'

import { fetchSession, login, logout, type Feature } from '@/api/auth.ts'
import { onSessionEnded } from '@/api/http.ts'
import { AuthContext, type AuthContextValue, type AuthState } from '@/auth/auth-context.ts'
import { setRouteFeatures } from '@/auth/feature-route.tsx'

const NO_FEATURES: ReadonlySet<Feature> = new Set()

/**
 * Asks the API once who this browser is signed in as and what the instance runs, then tracks
 * sign-in and sign-out. The features stay as they were across both.
 */
export function AuthProvider({ children }: { children: ReactNode }): ReactElement {
  const [state, setState] = useState<AuthState>({ status: 'checking' })
  const [features, setFeatures] = useState<ReadonlySet<Feature>>(NO_FEATURES)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    fetchSession(controller.signal).then(
      ({ user, features: enabled }) => {
        setRouteFeatures(enabled)
        setFeatures(enabled)
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

  useEffect(() => {
    onSessionEnded(() => {
      setState((current) =>
        current.status === 'signed-in' ? { status: 'signed-out', ended: true } : current,
      )
    })
    return () => {
      onSessionEnded(null)
    }
  }, [])

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
    () => ({ ...state, features, signIn, signOut, recheck }),
    [state, features, signIn, signOut, recheck],
  )
  return <AuthContext value={value}>{children}</AuthContext>
}

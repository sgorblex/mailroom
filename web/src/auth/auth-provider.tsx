import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from 'react'

import {
  changeConversations as saveConversations,
  changeIndexMaxAge as saveIndexMaxAge,
  changeMarkReadSubfolders as saveMarkReadSubfolders,
  changeTimeZone as saveTimeZone,
  fetchSession,
  login,
  logout,
  type Feature,
  type User,
} from '@/api/auth.ts'
import { onSessionEnded } from '@/api/http.ts'
import { AuthContext, type AuthContextValue, type AuthState } from '@/auth/auth-context.ts'
import { setRouteFeatures } from '@/auth/feature-route.tsx'
import { browserTimeZone } from '@/lib/time-zone.ts'

const NO_FEATURES: ReadonlySet<Feature> = new Set()

/**
 * Asks the API once who this browser is signed in as and what the instance runs, then tracks
 * sign-in and sign-out. The features stay as they were across both. A user with no time zone gets
 * the browser's, saved once per sign-in.
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

  const updateUser = useCallback((user: User) => {
    setState((current) =>
      current.status === 'signed-in' && current.user.username === user.username
        ? { status: 'signed-in', user }
        : current,
    )
  }, [])

  const filledFor = useRef<string | null>(null)
  useEffect(() => {
    if (state.status !== 'signed-in') {
      filledFor.current = null
      return
    }
    if (state.user.time_zone !== null) {
      return
    }
    const { username } = state.user
    if (filledFor.current === username) {
      return
    }
    filledFor.current = username
    saveTimeZone(browserTimeZone()).then(updateUser, () => undefined)
  }, [state, updateUser])

  const changeTimeZone = useCallback(
    async (timeZone: string | null) => {
      updateUser(await saveTimeZone(timeZone))
    },
    [updateUser],
  )

  const changeMarkReadSubfolders = useCallback(
    async (subfolders: boolean) => {
      updateUser(await saveMarkReadSubfolders(subfolders))
    },
    [updateUser],
  )

  const changeIndexMaxAge = useCallback(
    async (days: number | null) => {
      updateUser(await saveIndexMaxAge(days))
    },
    [updateUser],
  )

  const changeConversations = useCallback(
    async (conversations: boolean) => {
      updateUser(await saveConversations(conversations))
    },
    [updateUser],
  )

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
    () => ({ ...state, features, signIn, signOut, changeTimeZone, changeMarkReadSubfolders, changeIndexMaxAge, changeConversations, recheck }),
    [state, features, signIn, signOut, changeTimeZone, changeMarkReadSubfolders, changeIndexMaxAge, changeConversations, recheck],
  )
  return <AuthContext value={value}>{children}</AuthContext>
}

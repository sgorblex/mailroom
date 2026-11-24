import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'

import { ApiRequestError } from '@/api/http.ts'
import { askRefresh, refreshRunning, type RefreshScope } from '@/api/mail.ts'

import { MAIL_NS } from './i18n.ts'
import { ACCOUNT_NOT_FOUND, mailErrorMessage } from './messages.ts'
import { messagesKey } from './queries.ts'
import { RefreshContext } from './refresh-context.ts'
import { useToast } from './toast-context.ts'

/** How often a refresh under way is asked about. */
const POLL_MS = 1000
/** How long the button stays busy: past it, the list still takes what arrives as live updates. */
const WAIT_MS = 45_000

/** Resolves after `ms`, or as soon as `signal` aborts. */
function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = (): void => {
      window.clearTimeout(timer)
      signal.removeEventListener('abort', done)
      resolve()
    }
    const timer = window.setTimeout(done, ms)
    signal.addEventListener('abort', done)
  })
}

/** Follows the refresh to its end; `false` when it is still running after `WAIT_MS`. */
async function finished(refreshId: string, signal: AbortSignal): Promise<boolean> {
  const deadline = Date.now() + WAIT_MS
  while (Date.now() < deadline) {
    await pause(POLL_MS, signal)
    if (!(await refreshRunning(refreshId, signal))) {
      return true
    }
  }
  return false
}

/**
 * Refreshes the list `source` names, whose folders are `scope`'s: the server syncs those on the
 * index ahead of its other work, and the list's first page is read again once it has (at once
 * when none is, which reads the mail server). Showing another list lets go of it: the sync goes on.
 */
export function RefreshProvider({
  scope,
  source,
  children,
}: {
  scope: RefreshScope
  source: string
  children: ReactNode
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const queryClient = useQueryClient()
  const { show } = useToast()
  const [running, setRunning] = useState<string | null>(null)
  const current = useRef<AbortController | null>(null)
  useEffect(
    () => () => {
      current.current?.abort()
      current.current = null
    },
    [source],
  )
  const latest = useRef({ scope, t })
  useEffect(() => {
    latest.current = { scope, t }
  })

  const refresh = useCallback(() => {
    if (current.current !== null) {
      return
    }
    const controller = new AbortController()
    const { signal } = controller
    current.current = controller
    setRunning(source)
    const run = async (): Promise<void> => {
      try {
        const refreshId = await askRefresh(latest.current.scope, signal)
        const done = refreshId === null || (await finished(refreshId, signal))
        await queryClient.invalidateQueries({ queryKey: messagesKey(source, null) })
        if (!done && !signal.aborted) {
          show({ message: latest.current.t('refresh.slow') })
        }
      } catch (failure) {
        if (!signal.aborted) {
          const worded = latest.current.t
          const limited = failure instanceof ApiRequestError && failure.code === 'refresh_limited'
          show({
            message: limited
              ? worded('refresh.limited')
              : mailErrorMessage(worded, failure, worded('refresh.failed'), ACCOUNT_NOT_FOUND),
            tone: 'error',
          })
        }
      }
      // Another list's own refresh may have begun since.
      if (current.current === controller) {
        current.current = null
        setRunning(null)
      } else if (current.current === null) {
        setRunning(null)
      }
    }
    void run()
  }, [source, queryClient, show])

  const busy = running === source
  const value = useMemo(() => ({ busy, refresh }), [busy, refresh])
  return <RefreshContext value={value}>{children}</RefreshContext>
}

import { useCallback, useEffect, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { fetchHealth } from '@/api/health.ts'
import { apiErrorMessage } from '@/api/http.ts'
import { Button } from '@/components/ui/button.tsx'

type HealthState = { kind: 'checking' } | { kind: 'ok' } | { kind: 'failed'; message: string }

/** Placeholder home: proves the SPA reaches `/api/v1/health` through the proxy. */
export function HealthScreen(): ReactElement {
  const { t } = useTranslation()
  const [state, setState] = useState<HealthState>({ kind: 'checking' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    fetchHealth(controller.signal).then(
      () => {
        setState({ kind: 'ok' })
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setState({ kind: 'failed', message: apiErrorMessage(error, t('health.unknownError')) })
        }
      },
    )
    return () => {
      controller.abort()
    }
  }, [attempt, t])

  const retry = useCallback(() => {
    setState({ kind: 'checking' })
    setAttempt((value) => value + 1)
  }, [])

  return (
    <main className="mx-auto flex w-full max-w-xl flex-1 flex-col justify-center gap-4 p-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t('appName')}</h1>
      <section aria-labelledby="health-title" className="rounded-lg border bg-card p-4">
        <h2 id="health-title" className="text-sm font-medium text-muted-foreground">
          {t('health.title')}
        </h2>
        <p role="status" className="mt-2">
          {state.kind === 'checking' && t('health.checking')}
          {state.kind === 'ok' && t('health.ok')}
          {state.kind === 'failed' && t('health.failed', { message: state.message })}
        </p>
        {state.kind === 'failed' && (
          <Button className="mt-3" variant="outline" size="sm" onClick={retry}>
            {t('health.retry')}
          </Button>
        )}
      </section>
    </main>
  )
}

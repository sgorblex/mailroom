import { useCallback, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearch } from '@tanstack/react-router'

import { ApiRequestError, apiErrorMessage } from '@/api/http.ts'
import { decideConsentRequest, fetchConsentRequest, type ConsentRequest } from '@/api/oauth.ts'
import { useAuth } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { useLoad } from '@/lib/use-load.ts'

/** `/connect?request=<id>`, where the built-in authorization server sends an app's user. */
export function ConnectRoute(): ReactElement {
  const { request } = useSearch({ from: '/connect' })
  return <ConnectScreen key={request} requestId={request ?? null} />
}

/** The request is unknown, expired, another user's or already decided. */
const GONE = 'gone'

function leaveForTheApp(url: string): void {
  window.location.assign(url)
}

/**
 * The consent screen: who asks, where the browser goes back to, approve or deny. Signed out, the
 * root layout shows the sign-in screen in its place, which comes back here.
 */
export function ConnectScreen({
  requestId,
  leave = leaveForTheApp,
}: {
  requestId: string | null
  /** Where the browser goes once decided: the app's redirect URL. */
  leave?: (url: string) => void
}): ReactElement {
  const { t } = useTranslation()
  const load = useCallback(
    (signal: AbortSignal): Promise<ConsentRequest> =>
      requestId === null ? Promise.reject(new Error(GONE)) : fetchConsentRequest(requestId, signal),
    [requestId],
  )
  const { state, retry } = useLoad(load, (error) =>
    (error instanceof ApiRequestError && error.status === 404) ||
    (error instanceof Error && error.message === GONE)
      ? GONE
      : apiErrorMessage(error, t('connect.loadFailed')),
  )

  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col gap-6 p-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t('connect.title')}</h1>
      {state.kind === 'loading' && (
        <p role="status" className="text-muted-foreground">
          {t('connect.loading')}
        </p>
      )}
      {state.kind === 'failed' && state.message === GONE && <p role="alert">{t('connect.gone')}</p>}
      {state.kind === 'failed' && state.message !== GONE && (
        <div className="flex flex-col gap-3">
          <p role="alert">{state.message}</p>
          <Button className="self-start" variant="outline" size="sm" onClick={retry}>
            {t('connect.retry')}
          </Button>
        </div>
      )}
      {state.kind === 'loaded' && requestId !== null && (
        <ConsentCard requestId={requestId} request={state.value} leave={leave} />
      )}
    </main>
  )
}

function ConsentCard({
  requestId,
  request,
  leave,
}: {
  requestId: string
  request: ConsentRequest
  leave: (url: string) => void
}): ReactElement {
  const { t } = useTranslation()
  const auth = useAuth()
  const username = auth.status === 'signed-in' ? auth.user.username : ''
  const [pending, setPending] = useState<'approve' | 'deny' | null>(null)
  const [leaving, setLeaving] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  // Expired or answered meanwhile: nothing left to decide, so no buttons to press again.
  const [gone, setGone] = useState(false)
  const name = request.client_name ?? t('connect.unnamed')

  async function decide(decision: 'approve' | 'deny'): Promise<void> {
    setPending(decision)
    setFailure(null)
    try {
      const target = await decideConsentRequest(requestId, decision)
      setLeaving(true)
      leave(target)
    } catch (error) {
      const isGone = error instanceof ApiRequestError && error.status === 404
      setGone(isGone)
      setFailure(isGone ? t('connect.gone') : apiErrorMessage(error, t('connect.decideFailed')))
      setPending(null)
    }
  }

  return (
    <section aria-labelledby="connect-client" className="flex flex-col gap-4 rounded-lg border p-4">
      <h2 id="connect-client" className="text-lg font-medium break-words">
        {t('connect.asks', { name, username })}
      </h2>
      <p className="text-sm text-muted-foreground">
        {request.client_host === null
          ? t('connect.selfRegistered')
          : t('connect.describedBy', { host: request.client_host })}
      </p>
      <p className="text-sm">{t('connect.access')}</p>
      <p className="text-sm">
        {t('connect.returnsTo')} <strong className="break-all">{request.redirect_host}</strong>
      </p>
      <p className="text-sm text-muted-foreground">{t('connect.revoke')}</p>
      {failure !== null && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
      {leaving && (
        <p role="status" className="text-sm">
          {t('connect.leaving', { host: request.redirect_host })}
        </p>
      )}
      {!leaving && !gone && (
        <div className="flex gap-3">
          <Button disabled={pending !== null} onClick={() => void decide('approve')}>
            {t('connect.approve')}
          </Button>
          <Button variant="outline" disabled={pending !== null} onClick={() => void decide('deny')}>
            {t('connect.deny')}
          </Button>
        </div>
      )}
    </section>
  )
}

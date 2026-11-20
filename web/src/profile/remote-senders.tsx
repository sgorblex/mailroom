import { useCallback, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiRequestError, apiErrorMessage } from '@/api/http.ts'
import { listRemoteSenders, removeRemoteSender } from '@/api/mail.ts'
import { useTimeZone } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { formatDateTime } from '@/lib/format.ts'
import { useLoad } from '@/lib/use-load.ts'

/**
 * The senders whose remote images the user shows without asking, each removable. They are added
 * only from a message, beside its remote images, when DMARC passed for its sender.
 */
export function RemoteSenders(): ReactElement {
  const { t, i18n } = useTranslation()
  const timeZone = useTimeZone()
  const [reloadKey, setReloadKey] = useState(0)
  const [pending, setPending] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const load = useCallback((signal: AbortSignal) => listRemoteSenders(signal), [])
  const { state } = useLoad(load, (error) => apiErrorMessage(error, t('remoteSenders.loadFailed')), reloadKey)

  async function remove(address: string): Promise<void> {
    setPending(address)
    setFailure(null)
    try {
      await removeRemoteSender(address)
    } catch (error) {
      // A 404: already removed elsewhere, which is what the user asked for.
      if (!(error instanceof ApiRequestError && error.status === 404)) {
        setFailure(apiErrorMessage(error, t('remoteSenders.removeFailed')))
      }
    } finally {
      setPending(null)
      setReloadKey((value) => value + 1)
    }
  }

  const senders = state.kind === 'loaded' ? state.value : []
  return (
    <section aria-labelledby="remote-senders" className="flex flex-col gap-3">
      <h2 id="remote-senders" className="font-medium">
        {t('remoteSenders.title')}
      </h2>
      <p className="text-sm text-muted-foreground">{t('remoteSenders.intro')}</p>
      {state.kind === 'failed' && (
        <p role="alert" className="text-sm">
          {state.message}
        </p>
      )}
      {state.kind === 'loaded' && senders.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('remoteSenders.none')}</p>
      )}
      {senders.length > 0 && (
        <ul aria-label={t('remoteSenders.title')} className="flex flex-col divide-y rounded-lg border">
          {senders.map((sender) => (
            <li key={sender.address} className="flex items-center gap-3 p-3">
              <div className="mr-auto min-w-0">
                <p className="truncate text-sm font-medium">{sender.address}</p>
                <p className="text-xs text-muted-foreground">
                  {t('remoteSenders.remembered', {
                    when: formatDateTime(i18n.language, sender.created_at, timeZone),
                  })}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                aria-label={t('remoteSenders.removeSender', { address: sender.address })}
                disabled={pending !== null}
                onClick={() => void remove(sender.address)}
              >
                {t('remoteSenders.remove')}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {failure !== null && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
    </section>
  )
}

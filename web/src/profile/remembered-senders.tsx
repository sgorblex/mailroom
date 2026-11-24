import { useCallback, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiRequestError, apiErrorMessage } from '@/api/http.ts'
import {
  listRemoteSenders,
  listTextSenders,
  removeRemoteSender,
  removeTextSender,
  type RememberedSender,
} from '@/api/mail.ts'
import { useTimeZone } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { formatDateTime } from '@/lib/format.ts'
import { useLoad } from '@/lib/use-load.ts'

/**
 * The senders whose remote images the user shows without asking, each removable. They are added
 * only from a message, beside its remote images, when DMARC passed for its sender.
 */
export function RemoteSenders(): ReactElement {
  const { t } = useTranslation()
  return (
    <SenderList
      id="remote-senders"
      list={listRemoteSenders}
      remove={removeRemoteSender}
      labels={{
        title: t('remoteSenders.title'),
        intro: t('remoteSenders.intro'),
        none: t('remoteSenders.none'),
      }}
    />
  )
}

/**
 * The senders whose mail opens as text rather than HTML, each removable. They are added from a
 * message, by choosing Text, and removed by choosing HTML there too.
 */
export function TextSenders(): ReactElement {
  const { t } = useTranslation()
  return (
    <SenderList
      id="text-senders"
      list={listTextSenders}
      remove={removeTextSender}
      labels={{
        title: t('textSenders.title'),
        intro: t('textSenders.intro'),
        none: t('textSenders.none'),
      }}
    />
  )
}

interface Labels {
  title: string
  intro: string
  none: string
}

/** A list of remembered senders, by address, each removable. */
function SenderList({
  id,
  list,
  remove: removeSender,
  labels,
}: {
  id: string
  list: (signal?: AbortSignal) => Promise<RememberedSender[]>
  remove: (address: string) => Promise<void>
  labels: Labels
}): ReactElement {
  const { t, i18n } = useTranslation()
  const timeZone = useTimeZone()
  const [reloadKey, setReloadKey] = useState(0)
  const [pending, setPending] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const load = useCallback((signal: AbortSignal) => list(signal), [list])
  const { state } = useLoad(load, (error) => apiErrorMessage(error, t('rememberedSenders.loadFailed')), reloadKey)

  async function remove(address: string): Promise<void> {
    setPending(address)
    setFailure(null)
    try {
      await removeSender(address)
    } catch (error) {
      // A 404: already removed elsewhere, which is what the user asked for.
      if (!(error instanceof ApiRequestError && error.status === 404)) {
        setFailure(apiErrorMessage(error, t('rememberedSenders.removeFailed')))
      }
    } finally {
      setPending(null)
      setReloadKey((value) => value + 1)
    }
  }

  const senders = state.kind === 'loaded' ? state.value : []
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="font-medium">
        {labels.title}
      </h2>
      <p className="text-sm text-muted-foreground">{labels.intro}</p>
      {state.kind === 'failed' && (
        <p role="alert" className="text-sm">
          {state.message}
        </p>
      )}
      {state.kind === 'loaded' && senders.length === 0 && (
        <p className="text-sm text-muted-foreground">{labels.none}</p>
      )}
      {senders.length > 0 && (
        <ul aria-label={labels.title} className="flex flex-col divide-y rounded-lg border">
          {senders.map((sender) => (
            <li key={sender.address} className="flex items-center gap-3 p-3">
              <div className="mr-auto min-w-0">
                <p className="truncate text-sm font-medium">{sender.address}</p>
                <p className="text-xs text-muted-foreground">
                  {t('rememberedSenders.since', {
                    when: formatDateTime(i18n.language, sender.created_at, timeZone),
                  })}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                aria-label={t('rememberedSenders.removeSender', { address: sender.address })}
                disabled={pending !== null}
                onClick={() => void remove(sender.address)}
              >
                {t('rememberedSenders.remove')}
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

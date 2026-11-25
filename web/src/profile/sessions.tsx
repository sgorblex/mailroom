import { useCallback, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { apiErrorMessage, ApiRequestError } from '@/api/http.ts'
import { endOtherSessions, endSession, listSessions, type SignedInSession } from '@/api/sessions.ts'
import { useTimeZone } from '@/auth/auth-context.ts'
import { ConfirmRemoval } from '@/components/confirm-removal.tsx'
import { Button } from '@/components/ui/button.tsx'
import { formatDateTime } from '@/lib/format.ts'
import { useLoad } from '@/lib/use-load.ts'

/** What a confirm is asked for: one session's id, or every other one. */
type Ending = number | 'others'

/**
 * The browsers the user is signed in on, this one first. Each other one is signed out here after a
 * confirm, or all of them at once; this one by signing out. `reloadKey` changing lists them again.
 */
export function Sessions({ reloadKey }: { reloadKey: number }): ReactElement {
  const { t, i18n } = useTranslation()
  const timeZone = useTimeZone()
  const [ended, setEnded] = useState(0)
  const [failure, setFailure] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [confirming, setConfirming] = useState<Ending | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  // The buttons a confirm replaces, to give focus back to when it is cancelled.
  const triggers = useRef(new Map<Ending, HTMLButtonElement>())
  const load = useCallback((signal: AbortSignal) => listSessions(signal), [])
  const { state, retry } = useLoad(
    load,
    (error) => apiErrorMessage(error, t('sessions.loadFailed')),
    `${String(reloadKey)}-${String(ended)}`,
  )

  async function end(ending: Ending): Promise<void> {
    setPending(true)
    setFailure(null)
    try {
      await (ending === 'others' ? endOtherSessions() : endSession(ending))
    } catch (error) {
      // Gone already is what was asked for: the list shows it.
      if (!(error instanceof ApiRequestError && error.status === 404)) {
        setFailure(apiErrorMessage(error, t('sessions.endFailed')))
      }
    } finally {
      // The confirm goes, and with it the focus.
      heading.current?.focus()
      setPending(false)
      setConfirming(null)
      setEnded((value) => value + 1)
    }
  }

  function trigger(ending: Ending, label: string, name?: string): ReactElement {
    return (
      <Button
        ref={(button) => {
          if (button === null) {
            triggers.current.delete(ending)
          } else {
            triggers.current.set(ending, button)
          }
        }}
        variant="outline"
        size="sm"
        className="self-start"
        aria-label={name}
        disabled={pending}
        onClick={() => {
          setConfirming(ending)
          setFailure(null)
        }}
      >
        {label}
      </Button>
    )
  }

  function confirm(ending: Ending, question: string): ReactElement {
    return (
      <ConfirmRemoval
        question={question}
        confirmLabel={t('sessions.confirm')}
        cancelLabel={t('sessions.cancel')}
        pending={pending}
        onConfirm={() => void end(ending)}
        onCancel={() => {
          setConfirming(null)
          requestAnimationFrame(() => triggers.current.get(ending)?.focus())
        }}
      />
    )
  }

  function details(session: SignedInSession): string {
    const at = (iso: string): string => formatDateTime(i18n.language, iso, timeZone)
    return [
      t('sessions.lastSeen', { when: at(session.last_seen_at) }),
      t('sessions.signedIn', { when: at(session.created_at) }),
      ...(session.ip === null ? [] : [t('sessions.ip', { ip: session.ip })]),
    ].join(' · ')
  }

  const sessions = state.kind === 'loaded' ? state.value : []
  return (
    <section aria-labelledby="sessions" className="flex flex-col gap-3">
      <h3 id="sessions" ref={heading} tabIndex={-1} className="font-medium outline-none">
        {t('sessions.title')}
      </h3>
      <p className="text-sm text-muted-foreground">{t('sessions.intro')}</p>
      {state.kind === 'loading' && <p className="text-sm text-muted-foreground">{t('sessions.loading')}</p>}
      {state.kind === 'failed' && (
        <div className="flex flex-col items-start gap-3">
          <p role="alert" className="text-sm text-destructive">
            {state.message}
          </p>
          <Button variant="outline" size="sm" onClick={retry}>
            {t('sessions.retry')}
          </Button>
        </div>
      )}
      {sessions.length > 0 && (
        <ul aria-label={t('sessions.title')} className="flex flex-col divide-y rounded-lg border">
          {sessions.map((session) => {
            const device = session.device ?? t('sessions.unknownDevice')
            return (
              <li key={session.id} className="flex flex-col gap-2 p-3">
                <div className="flex items-center gap-3">
                  <div className="mr-auto min-w-0">
                    <p className="text-sm font-medium">
                      {device}
                      {session.current && (
                        <span className="ml-2 rounded-md bg-accent-soft px-1.5 py-0.5 text-xs text-accent-soft-foreground">
                          {t('sessions.thisDevice')}
                        </span>
                      )}
                    </p>
                    <p className="text-xs break-words text-muted-foreground">{details(session)}</p>
                  </div>
                  {!session.current &&
                    confirming !== session.id &&
                    trigger(
                      session.id,
                      t('sessions.end'),
                      t('sessions.endNamed', {
                        device,
                        when: formatDateTime(i18n.language, session.last_seen_at, timeZone),
                      }),
                    )}
                </div>
                {confirming === session.id && confirm(session.id, t('sessions.confirmEnd', { device }))}
              </li>
            )
          })}
        </ul>
      )}
      {failure !== null && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
      {confirming === 'others'
        ? confirm('others', t('sessions.confirmEndOthers'))
        : sessions.some((session) => !session.current) && trigger('others', t('sessions.endOthers'))}
    </section>
  )
}

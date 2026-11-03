import { useCallback, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { checkMessageHtml, messageHtmlUrl } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { useLoad } from '@/lib/use-load.ts'

import { MAIL_NS } from './i18n.ts'
import { mailErrorMessage } from './messages.ts'

/**
 * The sandbox of the HTML view: links may open a new top-level context, nothing else. Never
 * `allow-scripts` or `allow-same-origin`: the message keeps an opaque origin and runs no script.
 */
export const HTML_SANDBOX = 'allow-popups allow-popups-to-escape-sandbox'

/**
 * The API's standalone, sanitised HTML document in a sandboxed iframe, framed once a request for
 * it answered: a refusal is worded here, never shown inside the frame. Remote images stay
 * blocked until the reader asks, for this message only: the choice dies with the message view.
 */
export function HtmlBody({
  messageRef,
  subject,
  hasRemoteContent,
  remote,
  onShowRemote,
}: {
  messageRef: string
  subject: string
  hasRemoteContent: boolean
  remote: boolean
  onShowRemote: () => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const check = useCallback(
    async (signal: AbortSignal) => {
      await checkMessageHtml(messageRef, remote, signal)
      return remote
    },
    [messageRef, remote],
  )
  const { state, retry } = useLoad(check, (error) => mailErrorMessage(t, error, t('message.htmlFailed')))
  return (
    <section aria-label={t('message.body')} className="flex flex-1 flex-col gap-2">
      {hasRemoteContent && !remote && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border p-2 text-sm">
          <p className="mr-auto text-muted-foreground">{t('message.remoteBlocked')}</p>
          <Button variant="outline" size="sm" onClick={onShowRemote}>
            {t('message.showRemote')}
          </Button>
        </div>
      )}
      {remote && <p className="text-xs text-muted-foreground">{t('message.remoteShown')}</p>}
      {state.kind === 'loading' && (
        <p role="status" className="text-sm text-muted-foreground">
          {t('message.htmlLoading')}
        </p>
      )}
      {state.kind === 'failed' && (
        <div className="flex flex-col items-start gap-3">
          <p role="alert" className="text-sm">
            {state.message}
          </p>
          <Button variant="outline" size="sm" onClick={retry}>
            {t('auth.retry')}
          </Button>
        </div>
      )}
      {state.kind === 'loaded' && (
        <iframe
          title={t('message.htmlTitle', { subject: subject === '' ? t('message.noSubject') : subject })}
          src={messageHtmlUrl(messageRef, state.value)}
          sandbox={HTML_SANDBOX}
          referrerPolicy="no-referrer"
          className="min-h-[60vh] w-full flex-1 rounded-md border bg-white"
        />
      )}
    </section>
  )
}

import { useCallback, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Check } from 'lucide-react'

import { ApiRequestError } from '@/api/http.ts'
import { addRemoteSender, checkMessageHtml, messageHtmlUrl } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { useLoad } from '@/lib/use-load.ts'
import { cn } from '@/lib/utils.ts'
import { useDark } from '@/theme/use-dark.ts'

import { MAIL_NS } from './i18n.ts'
import { mailErrorMessage } from './messages.ts'

/**
 * The sandbox of the HTML view: links may open a new top-level context, nothing else. Never
 * `allow-scripts` or `allow-same-origin`: the message keeps an opaque origin and runs no script.
 */
export const HTML_SANDBOX = 'allow-popups allow-popups-to-escape-sandbox'

/**
 * Whether remote images are shown: `blocked`, for this `message` only (the choice dies with the
 * message view), or for its `sender`, whom the user remembers.
 */
export type RemoteChoice = 'blocked' | 'message' | 'sender'

/**
 * The API's standalone, sanitised HTML document in a sandboxed iframe, framed once its check
 * answered: a refusal is worded here, never shown inside the frame. Remote images stay
 * blocked until the reader asks, for this message or, when `canTrustSender` (DMARC passed for its
 * one From), always for its sender; the server checks that again before it remembers one. In a
 * dark theme the frame shows the server's `dark` document, unless the reader asks for the original
 * colours, a choice the message view keeps while the message is open: the same document, which
 * the frame's URL fragment switches without a request or a reload. The toggle shows only beside a
 * framed dark document, and a check marks it pressed, which a dark theme's secondary alone hardly
 * tells from outline.
 */
export function HtmlBody({
  messageRef,
  subject,
  remoteImages,
  loadableRemoteImages,
  choice,
  canTrustSender,
  onChoose,
  original,
  onToggleOriginal,
}: {
  messageRef: string
  subject: string
  remoteImages: number
  /** Of those, the ones `remote` shows: no button to show them when there are none. */
  loadableRemoteImages: number
  choice: RemoteChoice
  canTrustSender: boolean
  onChoose: (choice: RemoteChoice) => void
  /** The reader asked for the message's own colours: kept by the message view, as `choice` is. */
  original: boolean
  onToggleOriginal: () => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const remote = choice !== 'blocked'
  const darkTheme = useDark()
  const [trusting, setTrusting] = useState(false)
  const [trustFailure, setTrustFailure] = useState<string | null>(null)
  const check = useCallback(
    async (signal: AbortSignal) => {
      await checkMessageHtml(messageRef, remote, signal)
      return { remote }
    },
    [messageRef, remote],
  )
  const { state, retry } = useLoad(check, (error) => mailErrorMessage(t, error, t('message.htmlFailed')))

  async function trustSender(): Promise<void> {
    setTrusting(true)
    setTrustFailure(null)
    try {
      await addRemoteSender(messageRef)
      onChoose('sender')
    } catch (error) {
      setTrustFailure(
        error instanceof ApiRequestError && error.code === 'sender_not_verified'
          ? t('message.senderNotVerified')
          : mailErrorMessage(t, error, t('message.trustSenderFailed')),
      )
    } finally {
      setTrusting(false)
    }
  }

  return (
    <section aria-label={t('message.body')} className="flex flex-1 flex-col gap-2">
      {remoteImages > 0 && !remote && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border p-2 text-sm">
          <p className="mr-auto text-muted-foreground">{t('message.remoteBlocked', { count: remoteImages })}</p>
          {loadableRemoteImages > 0 && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                onChoose('message')
              }}
            >
              {t('message.showRemote')}
            </Button>
          )}
          {loadableRemoteImages > 0 && canTrustSender && (
            <Button
              variant="outline"
              size="sm"
              className="aria-disabled:opacity-50"
              aria-disabled={trusting}
              onClick={() => {
                if (!trusting) {
                  void trustSender()
                }
              }}
            >
              {t('message.trustSender')}
            </Button>
          )}
          {trustFailure !== null && (
            <p role="alert" className="basis-full text-destructive">
              {trustFailure}
            </p>
          )}
        </div>
      )}
      {remote && loadableRemoteImages > 0 && (
        <p className="text-xs text-muted-foreground">
          {t(choice === 'sender' ? 'message.remoteShownSender' : 'message.remoteShown', {
            count: loadableRemoteImages,
          })}
        </p>
      )}
      {/* Only beside a dark document in the frame: next to an error or a light one it does nothing. */}
      {state.kind === 'loaded' && darkTheme && (
        <Button
          variant={original ? 'secondary' : 'outline'}
          size="xs"
          className="self-start"
          aria-pressed={original}
          onClick={() => {
            onToggleOriginal()
          }}
        >
          {original && <Check aria-hidden="true" />}
          {t('message.originalColours')}
        </Button>
      )}
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
          src={messageHtmlUrl(messageRef, state.value.remote, darkTheme, original)}
          sandbox={HTML_SANDBOX}
          referrerPolicy="no-referrer"
          className={cn(
            'min-h-[60vh] w-full flex-1 rounded-md border',
            darkTheme && !original ? 'bg-[#121212]' : 'bg-white',
          )}
        />
      )}
    </section>
  )
}

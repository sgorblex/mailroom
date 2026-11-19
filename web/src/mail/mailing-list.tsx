import { useContext, useId, useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { Link } from '@tanstack/react-router'
import { ExternalLink, List } from 'lucide-react'

import { ApiRequestError } from '@/api/http.ts'
import { unsubscribe, type MessageView, type Unsubscribed } from '@/api/mail.ts'
import { AuthContext } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { buttonVariants } from '@/components/ui/button-variants.ts'
import { Dialog } from '@/components/ui/dialog.tsx'

import { useCompose } from './compose-context.ts'
import { MAIL_NS } from './i18n.ts'
import { httpsUrl, listName, unsubscribeWay } from './list-unsubscribe.ts'
import { mailErrorMessage } from './messages.ts'

/** The server's refusals of a one-click unsubscribe, with their wording key. */
const REFUSALS: Record<string, string> = {
  unsubscribe_unavailable: 'mailingList.unavailable',
  unsubscribe_repeated: 'mailingList.repeated',
  unsubscribe_not_https: 'mailingList.notHttps',
  unsubscribe_unverified: 'mailingList.unverified',
  unsubscribe_not_signed: 'mailingList.notSigned',
}

/**
 * The list a message came from, as a header row: its name (plain text), its archive when that is
 * `https:`, and Unsubscribe, which acts only when the reader asks.
 */
export function MailingListRow({ view }: { view: MessageView }): ReactElement | null {
  const { t } = useTranslation(MAIL_NS)
  const compose = useCompose()
  const filters = useContext(AuthContext)?.features.has('filters') === true
  const [confirming, setConfirming] = useState(false)
  const hintId = useId()
  const list = view.headers.mailing_list
  if (list === null) {
    return null
  }
  const name = listName(list) ?? t('mailingList.unnamed')
  const archive = httpsUrl(list.archive)
  const way = unsubscribeWay(view, compose.canCompose(view.account_id))
  return (
    <>
      <dt className="text-muted-foreground">{t('mailingList.label')}</dt>
      <dd className="flex min-w-0 flex-col gap-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="inline-flex max-w-full items-center gap-1 rounded-md border bg-muted px-1.5 py-0.5 text-xs break-all">
            <List aria-hidden="true" className="size-3 shrink-0" />
            {name}
          </span>
          {archive !== null && (
            <a href={archive.href} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs underline">
              {t('mailingList.archive')}
              <ExternalLink aria-hidden="true" className="size-3" />
            </a>
          )}
          {way?.kind === 'oneClick' && (
            <Button
              variant="outline"
              size="xs"
              onClick={() => {
                setConfirming(true)
              }}
            >
              {t('mailingList.unsubscribe')}
            </Button>
          )}
          {way?.kind === 'mail' && (
            <Button
              variant="outline"
              size="xs"
              aria-disabled={!way.allowed}
              aria-describedby={way.allowed ? undefined : hintId}
              onClick={() => {
                if (way.allowed) {
                  compose.start({ kind: 'unsubscribe', account: view.account_id, ref: view.ref })
                }
              }}
            >
              {t('mailingList.unsubscribeByMail')}
            </Button>
          )}
          {way?.kind === 'link' && (
            <a href={way.url} target="_blank" rel="noopener noreferrer" className={buttonVariants({ variant: 'outline', size: 'xs' })}>
              {t('mailingList.unsubscribeOnSite')}
              <ExternalLink aria-hidden="true" />
            </a>
          )}
          {way !== null && filters && (
            <Link to="/filters" className="text-xs underline">
              {t('mailingList.filterInstead')}
            </Link>
          )}
        </span>
        {way?.kind === 'mail' && !way.allowed && (
          <span id={hintId} className="text-xs text-muted-foreground">
            {t('compose.offHint')}
          </span>
        )}
        {confirming && way?.kind === 'oneClick' && (
          <OneClickDialog
            messageRef={view.ref}
            name={name}
            host={way.host}
            onClose={() => {
              setConfirming(false)
            }}
          />
        )}
      </dd>
    </>
  )
}

type DialogState =
  | { kind: 'asking' }
  | { kind: 'sending' }
  | { kind: 'done'; answer: Unsubscribed }
  | { kind: 'failed'; message: string }

function answerText(t: TFunction, answer: Unsubscribed, host: string): string {
  switch (answer.outcome) {
    case 'ok':
      return t('mailingList.ok', { host })
    case 'rejected':
      return t('mailingList.rejected', { host, status: answer.status ?? '' })
    case 'refused':
      return t('mailingList.refused', { host })
    case 'failed':
      return t('mailingList.failed', { host })
  }
}

function failureText(t: TFunction, error: unknown): string {
  const key = error instanceof ApiRequestError && error.code !== undefined ? REFUSALS[error.code] : undefined
  return key === undefined ? mailErrorMessage(t, error, t('mailingList.requestFailed')) : t(key)
}

/** Asks before the one request, then says how the list answered. */
function OneClickDialog({
  messageRef,
  name,
  host,
  onClose,
}: {
  messageRef: string
  name: string
  host: string
  onClose: () => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [state, setState] = useState<DialogState>({ kind: 'asking' })
  const title = t('mailingList.confirmTitle', { name })

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (state.kind !== 'asking') {
      return
    }
    setState({ kind: 'sending' })
    try {
      setState({ kind: 'done', answer: await unsubscribe(messageRef) })
    } catch (error) {
      setState({ kind: 'failed', message: failureText(t, error) })
    }
  }

  function cancel(): void {
    if (state.kind !== 'sending') {
      onClose()
    }
  }

  const asking = state.kind === 'asking' || state.kind === 'sending'
  return (
    <Dialog label={title} onCancel={cancel}>
      <form noValidate onSubmit={(event) => void submit(event)} className="flex w-96 max-w-full flex-col gap-3">
        <h2 className="text-base font-medium break-words">{title}</h2>
        {asking && <p className="text-sm">{t('mailingList.confirmQuestion', { host })}</p>}
        {state.kind === 'sending' && (
          <p role="status" className="text-sm text-muted-foreground">
            {t('mailingList.sending')}
          </p>
        )}
        {state.kind === 'done' && (
          <p role={state.answer.outcome === 'ok' ? 'status' : 'alert'} className="text-sm">
            {answerText(t, state.answer, host)}
          </p>
        )}
        {state.kind === 'failed' && (
          <p role="alert" className="text-sm text-destructive">
            {state.message}
          </p>
        )}
        <div className="flex justify-end gap-2">
          {asking ? (
            <>
              <Button type="button" variant="outline" size="sm" aria-disabled={state.kind === 'sending'} onClick={cancel}>
                {t('mailingList.cancel')}
              </Button>
              <Button type="submit" size="sm" aria-disabled={state.kind === 'sending'}>
                {t('mailingList.unsubscribe')}
              </Button>
            </>
          ) : (
            <Button type="button" size="sm" onClick={onClose}>
              {t('mailingList.close')}
            </Button>
          )}
        </div>
      </form>
    </Dialog>
  )
}

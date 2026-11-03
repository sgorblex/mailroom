import { useCallback, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'

import { readMessage, type MailAddress, type MessageView as MessageData } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { formatDateTime } from '@/lib/format.ts'
import { useLoad } from '@/lib/use-load.ts'
import { useUnmountSignal } from '@/lib/use-unmount-signal.ts'

import { Attachments } from './attachments.tsx'
import { addressFull } from './format.ts'
import { HtmlBody } from './html-body.tsx'
import { MAIL_NS } from './i18n.ts'
import { mailErrorMessage } from './messages.ts'

/** One message, mounted anew (by key) for each: its view choices are never carried over. */
export function MessageView({ messageRef }: { messageRef: string }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [includeQuoted, setIncludeQuoted] = useState(false)
  // The remote images choice lives as long as this message's view, whatever is shown in it.
  const [remote, setRemote] = useState(false)
  // Each view says which choice it was read with: until the new one answers, the old one stays,
  // and nothing may page it with the new choice.
  const load = useCallback(
    async (signal: AbortSignal) => ({
      view: await readMessage(messageRef, { includeQuoted }, signal),
      includeQuoted,
    }),
    [messageRef, includeQuoted],
  )
  const { state, retry } = useLoad(load, (error) => mailErrorMessage(t, error, t('message.loadFailed')))
  const pending = state.kind === 'loading' || (state.kind === 'loaded' && state.value.includeQuoted !== includeQuoted)

  return (
    <article className="flex flex-1 flex-col gap-4" aria-busy={pending}>
      <Link
        to="/mail"
        search={(previous) => ({ ...previous, message: undefined })}
        className="flex items-center gap-1 self-start text-sm text-muted-foreground hover:text-foreground lg:hidden"
      >
        <ArrowLeft aria-hidden="true" className="size-4" />
        {t('message.back')}
      </Link>
      {state.kind === 'loading' && (
        <p role="status" className="text-sm text-muted-foreground">
          {t('message.loading')}
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
        <LoadedMessage
          view={state.value.view}
          includeQuoted={state.value.includeQuoted}
          pending={pending}
          onToggleQuoted={() => {
            setIncludeQuoted(!state.value.includeQuoted)
          }}
          remote={remote}
          onShowRemote={() => {
            setRemote(true)
          }}
        />
      )}
    </article>
  )
}

function LoadedMessage({
  view,
  includeQuoted,
  pending,
  onToggleQuoted,
  remote,
  onShowRemote,
}: {
  view: MessageData
  /** What `view` was read with. */
  includeQuoted: boolean
  /** A reload with the other choice is on its way. */
  pending: boolean
  onToggleQuoted: () => void
  remote: boolean
  onShowRemote: () => void
}): ReactElement {
  const { t, i18n } = useTranslation(MAIL_NS)
  // The text is the default; a message whose text body is empty opens on its HTML.
  const [html, setHtml] = useState(view.has_html && view.text.content === '')

  return (
    <>
      <header className="flex flex-col gap-2">
        <h2 className="text-xl font-semibold tracking-tight break-words">
          {view.subject === '' ? t('message.noSubject') : view.subject}
        </h2>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
          <AddressRow label={t('message.from')} addresses={view.from === null ? [] : [view.from]} />
          <AddressRow label={t('message.to')} addresses={view.to} />
          <AddressRow label={t('message.cc')} addresses={view.cc} />
          <AddressRow label={t('message.replyTo')} addresses={view.reply_to} />
          {view.date !== null && (
            <>
              <dt className="text-muted-foreground">{t('message.date')}</dt>
              <dd>
                <time dateTime={view.date}>{formatDateTime(i18n.language, view.date)}</time>
              </dd>
            </>
          )}
        </dl>
      </header>
      {view.has_html && (
        <div role="group" aria-label={t('message.format')} className="flex gap-1 self-start rounded-md border p-0.5">
          <Button
            variant={html ? 'ghost' : 'secondary'}
            size="xs"
            aria-pressed={!html}
            onClick={() => {
              setHtml(false)
            }}
          >
            {t('message.showText')}
          </Button>
          <Button
            variant={html ? 'secondary' : 'ghost'}
            size="xs"
            aria-pressed={html}
            onClick={() => {
              setHtml(true)
            }}
          >
            {t('message.showHtml')}
          </Button>
        </div>
      )}
      {html ? (
        <HtmlBody
          messageRef={view.ref}
          subject={view.subject}
          hasRemoteContent={view.has_remote_content}
          remote={remote}
          onShowRemote={onShowRemote}
        />
      ) : (
        <TextBody
          key={String(includeQuoted)}
          view={view}
          includeQuoted={includeQuoted}
          pending={pending}
          onToggleQuoted={onToggleQuoted}
        />
      )}
      {(view.attachments.length > 0 || view.calendar !== null) && (
        <Attachments messageRef={view.ref} attachments={view.attachments} calendar={view.calendar} />
      )}
    </>
  )
}

function AddressRow({ label, addresses }: { label: string; addresses: MailAddress[] }): ReactElement | null {
  if (addresses.length === 0) {
    return null
  }
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{addresses.map(addressFull).join(', ')}</dd>
    </>
  )
}

type More = { kind: 'idle' } | { kind: 'loading' } | { kind: 'failed'; message: string }

/** The text body, a window at a time ("Show more"), as plain text: never parsed as HTML. */
function TextBody({
  view,
  includeQuoted,
  pending,
  onToggleQuoted,
}: {
  view: MessageData
  includeQuoted: boolean
  pending: boolean
  onToggleQuoted: () => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [later, setLater] = useState<{ content: string; next: number | null }[]>([])
  const [more, setMore] = useState<More>({ kind: 'idle' })
  const unmountSignal = useUnmountSignal()
  const content = [view.text.content, ...later.map((window) => window.content)].join('')
  const next = later.length === 0 ? view.text.next_offset : (later[later.length - 1]?.next ?? null)

  async function showMore(offset: number): Promise<void> {
    setMore({ kind: 'loading' })
    const signal = unmountSignal()
    try {
      const window = await readMessage(view.ref, { offset, includeQuoted }, signal)
      setLater((value) => [...value, { content: window.text.content, next: window.text.next_offset }])
      setMore({ kind: 'idle' })
    } catch (error) {
      if (!signal.aborted) {
        setMore({ kind: 'failed', message: mailErrorMessage(t, error, t('message.loadFailed')) })
      }
    }
  }

  return (
    <section aria-label={t('message.body')} className="flex flex-col gap-3">
      {view.text.removed_chars > 0 && (
        <p className="text-xs text-muted-foreground">
          {t('message.removedChars', { count: view.text.removed_chars })}
        </p>
      )}
      {view.text.incomplete && <p className="text-xs text-muted-foreground">{t('message.incomplete')}</p>}
      {content === '' ? (
        <p className="text-sm text-muted-foreground">{t('message.noText')}</p>
      ) : (
        <div className="text-sm leading-relaxed break-words whitespace-pre-wrap">{content}</div>
      )}
      {more.kind === 'failed' && (
        <p role="alert" className="text-sm text-destructive">
          {more.message}
        </p>
      )}
      {view.text.quoted_omitted && <p className="text-xs text-muted-foreground">{t('message.quotedHidden')}</p>}
      <div className="flex flex-wrap gap-2">
        {next !== null && (
          <Button
            variant="outline"
            size="sm"
            aria-disabled={more.kind === 'loading' || pending}
            onClick={() => {
              if (more.kind !== 'loading' && !pending) {
                void showMore(next)
              }
            }}
          >
            {more.kind === 'loading' ? t('message.loadingMore') : t('message.showMore')}
          </Button>
        )}
        {(view.text.quoted_omitted || includeQuoted) && (
          <Button
            variant="ghost"
            size="sm"
            aria-disabled={pending}
            onClick={() => {
              if (!pending) {
                onToggleQuoted()
              }
            }}
          >
            {includeQuoted ? t('message.hideQuoted') : t('message.showQuoted')}
          </Button>
        )}
      </div>
    </section>
  )
}

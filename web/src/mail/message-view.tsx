import { useEffect, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { ArrowLeft, ShieldAlert, ShieldCheck, ShieldQuestionMark, TriangleAlert } from 'lucide-react'

import { readMessage, type MailAddress, type MessageView as MessageData } from '@/api/mail.ts'
import { useTimeZone } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { formatDateTime } from '@/lib/format.ts'
import { useUnmountSignal } from '@/lib/use-unmount-signal.ts'

import { Attachments } from './attachments.tsx'
import { useCompose } from './compose-context.ts'
import { addressFull } from './format.ts'
import { HtmlBody, type RemoteChoice } from './html-body.tsx'
import { MAIL_NS } from './i18n.ts'
import { MailingListRow } from './mailing-list.tsx'
import { mailErrorMessage } from './messages.ts'
import { fetchPage, messageKey, READ_ON_MOUNT } from './queries.ts'
import { ReplyActions } from './reply-actions.tsx'
import { authenticationState, recipientSummary, resultText } from './sender-checks.ts'
import { isSameFolder, loadedFolders, roleFolder, useTriage } from './triage-context.ts'

/** One message, mounted anew (by key) for each: its view choices are never carried over. */
export function MessageView({ messageRef }: { messageRef: string }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [includeQuoted, setIncludeQuoted] = useState(false)
  // The remote images choice lives as long as this message's view, whatever is shown in it.
  const [remote, setRemote] = useState<RemoteChoice>('blocked')
  // Each view says which choice it was read with: until the new one answers, the old one stays,
  // and nothing may page it with the new choice.
  const query = useQuery({
    queryKey: messageKey(messageRef, 0, includeQuoted),
    queryFn: async ({ signal }) => ({ view: await readMessage(messageRef, { includeQuoted }, signal), includeQuoted }),
    placeholderData: keepPreviousData,
    ...READ_ON_MOUNT,
  })
  const state = query.status
  const pending = state === 'pending' || (state === 'success' && query.data.includeQuoted !== includeQuoted)
  const { setOpen, flagsOf, run, folders, loadFolders, visibleRows } = useTriage()
  const { canCompose } = useCompose()
  const navigate = useNavigate()
  const marked = useRef(false)
  const view = state === 'success' ? query.data.view : null
  const accountFolders = view === null ? undefined : folders[view.account_id]
  // The user's own mail: no server checked who sent it on the way in.
  const own =
    view !== null &&
    (['sent', 'drafts'] as const).some((role) => {
      const folder = roleFolder(loadedFolders(accountFolders), role)
      return folder !== undefined && isSameFolder(folder.path, view.folder)
    })

  // The toolbar acts on the open message; opening an unread one marks it read, once, and a
  // failure leaves it unread without a word. A draft stays as it is, and opens in the composer
  // instead, unless its account can't save one (send mode off).
  useEffect(() => {
    if (view === null) {
      return
    }
    // Acted on as its row in the list, under the folder it was listed under.
    const listedFolder = visibleRows().find((row) => row.ref === view.ref)?.listed_folder
    const target = { ref: view.ref, account_id: view.account_id, folder: view.folder, listed_folder: listedFolder, flags: view.flags, from: view.from }
    setOpen(target)
    loadFolders(view.account_id, false)
    if (accountFolders === undefined || accountFolders.kind === 'loading' || marked.current) {
      return
    }
    // Decided on the first load only: marked unread while open, it stays so.
    marked.current = true
    const drafts = roleFolder(loadedFolders(accountFolders), 'drafts')
    if (drafts !== undefined && isSameFolder(drafts.path, view.folder) && canCompose(view.account_id)) {
      void navigate({
        to: '/mail',
        search: (previous) => ({ ...previous, message: undefined, compose: view.ref }),
        replace: true,
      })
      return
    }
    if (!view.flags.draft && !flagsOf(target).seen) {
      void run({ kind: 'flag', change: { seen: true } }, [target], { silent: true })
    }
  }, [view, accountFolders, canCompose, navigate, loadFolders, setOpen, flagsOf, run, visibleRows])
  useEffect(
    () => () => {
      setOpen(null)
    },
    [setOpen],
  )

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
      {state === 'pending' && (
        <p role="status" className="text-sm text-muted-foreground">
          {t('message.loading')}
        </p>
      )}
      {state === 'error' && (
        <div className="flex flex-col items-start gap-3">
          <p role="alert" className="text-sm">
            {mailErrorMessage(t, query.error, t('message.loadFailed'))}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void query.refetch()
            }}
          >
            {t('auth.retry')}
          </Button>
        </div>
      )}
      {state === 'success' && (
        <LoadedMessage
          view={query.data.view}
          own={own}
          includeQuoted={query.data.includeQuoted}
          pending={pending}
          onToggleQuoted={() => {
            setIncludeQuoted(!query.data.includeQuoted)
          }}
          remote={query.data.view.remote_trusted ? 'sender' : remote}
          onChooseRemote={setRemote}
        />
      )}
    </article>
  )
}

function LoadedMessage({
  view,
  own,
  includeQuoted,
  pending,
  onToggleQuoted,
  remote,
  onChooseRemote,
}: {
  view: MessageData
  /** It is in the account's Sent or Drafts folder: it has no authentication to show. */
  own: boolean
  /** What `view` was read with. */
  includeQuoted: boolean
  /** A reload with the other choice is on its way. */
  pending: boolean
  onToggleQuoted: () => void
  remote: RemoteChoice
  onChooseRemote: (choice: RemoteChoice) => void
}): ReactElement {
  const { t, i18n } = useTranslation(MAIL_NS)
  const timeZone = useTimeZone()
  // The text is the default; a message whose text body is empty opens on its HTML.
  const [html, setHtml] = useState(view.has_html && view.text.content === '')

  return (
    <>
      <header className="flex flex-col gap-2">
        <h2 className="text-xl font-semibold tracking-tight break-words">
          {view.subject === '' ? t('message.noSubject') : view.subject}
        </h2>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
          <AddressRow label={t('message.from')} addresses={view.from === null ? [] : [view.from]}>
            {view.sender_mismatch && view.from !== null && (
              <p className="flex items-start gap-1 text-destructive">
                <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                {t('message.senderMismatch', { address: view.from.address })}
              </p>
            )}
          </AddressRow>
          {!own && (
            <>
              <dt className="text-muted-foreground">{t('message.authentication')}</dt>
              <dd>
                <Authentication authentication={view.authentication} />
              </dd>
            </>
          )}
          {view.sent_to !== null && (
            <>
              <dt className="text-muted-foreground">{t('message.sentTo')}</dt>
              <dd className="min-w-0 break-words">{view.sent_to}</dd>
            </>
          )}
          <Recipients view={view} />
          <AddressRow label={t('message.replyTo')} addresses={view.reply_to} />
          {view.date !== null && (
            <>
              <dt className="text-muted-foreground">{t('message.date')}</dt>
              <dd>
                <time dateTime={view.date}>{formatDateTime(i18n.language, view.date, timeZone)}</time>
              </dd>
            </>
          )}
          <MailingListRow view={view} />
        </dl>
      </header>
      <ReplyActions account={view.account_id} messageRef={view.ref} />
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
          remoteImages={view.remote_images}
          loadableRemoteImages={view.loadable_remote_images}
          choice={remote}
          canTrustSender={view.authentication.dmarc_pass}
          onChoose={onChooseRemote}
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

function AddressRow({
  label,
  addresses,
  children,
}: {
  label: string
  addresses: MailAddress[]
  children?: ReactNode
}): ReactElement | null {
  if (addresses.length === 0) {
    return null
  }
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">
        {addresses.map(addressFull).join(', ')}
        {children}
      </dd>
    </>
  )
}

/** To and Cc in full, or summed up when there are many until the reader asks for them. */
function Recipients({ view }: { view: MessageData }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [expanded, setExpanded] = useState(false)
  const summary = recipientSummary(view.to, view.cc, view.sent_to)
  // One toggle in one place for both states, so the focus stays on it.
  return (
    <>
      {summary === null || expanded ? (
        <>
          <AddressRow label={t('message.to')} addresses={view.to} />
          <AddressRow label={t('message.cc')} addresses={view.cc} />
        </>
      ) : (
        <>
          <dt className="text-muted-foreground">{t('message.recipients')}</dt>
          <dd>
            {summary.you
              ? t('message.youAndOthers', { count: summary.others })
              : t('message.recipientCount', { count: summary.others })}
          </dd>
        </>
      )}
      {summary !== null && (
        <dd className="col-start-2">
          <Button
            variant="ghost"
            size="xs"
            aria-expanded={expanded}
            onClick={() => {
              setExpanded(!expanded)
            }}
          >
            {expanded ? t('message.hideRecipients') : t('message.showRecipients')}
          </Button>
        </dd>
      )}
    </>
  )
}

/** The account's own authentication server's verdict, with the results it gave when it gave any. */
function Authentication({ authentication }: { authentication: MessageData['authentication'] }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const state = authenticationState(authentication)
  if (state === 'unverified') {
    return (
      <span className="flex flex-col">
        <span className="flex items-start gap-1 text-muted-foreground">
          <ShieldQuestionMark aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {t('message.authUnverified')}
        </span>
        <span className="text-xs text-muted-foreground">{t('message.authUnverifiedHint')}</span>
      </span>
    )
  }
  const none = t('message.authNone')
  const results = t('message.authResults', {
    spf: resultText(authentication.spf ?? none, authentication.spf_domain),
    dkim: resultText(authentication.dkim ?? none, authentication.dkim_domain),
    dmarc:
      authentication.dmarc === 'pass' && !authentication.dmarc_pass
        ? t('message.authDmarcNotThisFrom')
        : (authentication.dmarc ?? none),
  })
  const Icon = state === 'pass' ? ShieldCheck : ShieldAlert
  return (
    <span className="flex flex-col">
      <span className={`flex items-start gap-1 ${state === 'fail' ? 'text-destructive' : ''}`}>
        <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        {state === 'pass'
          ? t('message.authPass', { domain: authentication.from_domain ?? '' })
          : state === 'fail'
            ? t('message.authFail')
            : t('message.authUnconfirmed')}
      </span>
      <span className="text-xs text-muted-foreground">{results}</span>
    </span>
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
  const queryClient = useQueryClient()
  const content = [view.text.content, ...later.map((window) => window.content)].join('')
  const next = later.length === 0 ? view.text.next_offset : (later[later.length - 1]?.next ?? null)

  async function showMore(offset: number): Promise<void> {
    setMore({ kind: 'loading' })
    const signal = unmountSignal()
    try {
      const window = await fetchPage(
        queryClient,
        messageKey(view.ref, offset, includeQuoted),
        (querySignal) => readMessage(view.ref, { offset, includeQuoted }, querySignal),
        signal,
      )
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

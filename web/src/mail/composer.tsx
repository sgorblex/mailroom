import { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from '@tanstack/react-router'
import { X } from 'lucide-react'

import { ApiRequestError } from '@/api/http.ts'
import { deleteDraft, type MailAddress } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { Dialog } from '@/components/ui/dialog.tsx'
import { Input } from '@/components/ui/input.tsx'
import { Textarea } from '@/components/ui/textarea.tsx'
import { useLoad } from '@/lib/use-load.ts'
import { useUnmountSignal } from '@/lib/use-unmount-signal.ts'

import { AttachmentTray } from './attachment-tray.tsx'
import { useCompose } from './compose-context.ts'
import { isBlank, type ComposeForm, type TrayItem } from './compose-form.ts'
import type { ComposeSession, ComposeSource } from './compose-session.ts'
import { DraftModel, loadStart, type ComposeStart, type DraftView } from './draft-model.ts'
import { MAIL_NS } from './i18n.ts'
import { IdentitySelect } from './identity-select.tsx'
import { composeErrorMessage, mailCodeMessage, mailErrorMessage, sentMessage } from './messages.ts'
import { RecipientInput, type Typed } from './recipient-input.tsx'
import { commitTyped } from './recipients.ts'
import { followSend, runSend, type SendEnd } from './send-flow.ts'
import { SendStatus, type SendState } from './send-status.tsx'
import { loadedFolders, roleFolder, useAccountFolders } from './triage-context.ts'

const MAX_SUBJECT = 998
const MAX_TEXT = 100_000
/** The server's default, for an account the API says nothing about. */
const DEFAULT_ATTACHMENT_MB = 10

const RECIPIENT_FIELDS = ['to', 'cc', 'bcc'] as const
type RecipientField = (typeof RECIPIENT_FIELDS)[number]

const NOTHING_TYPED: Record<RecipientField, Typed> = {
  to: { text: '', invalid: false },
  cc: { text: '', invalid: false },
  bcc: { text: '', invalid: false },
}

const TITLES: Record<ComposeSource['kind'], string> = {
  new: 'compose.newMessage',
  reply: 'compose.reply',
  reply_all: 'compose.replyAll',
  forward: 'compose.forward',
  draft: 'compose.draft',
  recover: 'compose.draft',
}

function sendStateOf(send: ComposeStart['send']): SendState {
  switch (send?.state) {
    case 'sending':
      return { kind: 'sending' }
    case 'sent':
      return { kind: 'sent' }
    case 'failed':
      return { kind: 'failed', code: send.code }
    case 'unknown':
      return { kind: 'unknown' }
    case undefined:
      return { kind: 'idle' }
  }
}

/** A send of the draft's key that the server will not let a new version of one written elsewhere leave behind. */
function holdsTheDraft(send: ComposeStart['send']): boolean {
  return send !== null && send.state !== 'failed'
}

/** The editor of the session, in the third pane, once what it starts from is read. */
export function Composer({ session }: { session: ComposeSession }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { account, close } = useCompose()
  // A session starts once: every save replaces the draft it opened, so reading that again could
  // only fail. `account` changes with anything the context holds, and must not start it over.
  const accountOf = useRef(account)
  useEffect(() => {
    accountOf.current = account
  })
  const load = useCallback((signal: AbortSignal) => loadStart(session, accountOf.current, signal), [session])
  const { state, retry } = useLoad(load, (error) => mailErrorMessage(t, error, t('compose.loadFailed')))
  const nothing = state.kind === 'loaded' && state.value === null

  // Reloaded before anything was saved: there is nothing to bring back.
  useEffect(() => {
    if (nothing) {
      close()
    }
  }, [nothing, close])

  if (state.kind === 'loaded' && state.value !== null) {
    return <Editor session={session} start={state.value} />
  }
  return (
    <div className="flex flex-col items-start gap-3">
      {state.kind === 'failed' ? (
        <>
          <p role="alert" className="text-sm">
            {state.message}
          </p>
          <div className="flex gap-2">
            <Button type="button" variant="outline" size="sm" onClick={retry}>
              {t('auth.retry')}
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={close}>
              {t('compose.close')}
            </Button>
          </div>
        </>
      ) : (
        <p role="status" className="text-sm text-muted-foreground">
          {t('compose.loading')}
        </p>
      )}
    </div>
  )
}

function SaveStatus({
  view,
  sending,
  limit,
  onSave,
}: {
  view: DraftView
  sending: boolean
  /** The limit the form is over, if any, in words. */
  limit: string | null
  onSave: () => void
}): ReactElement | null {
  const { t } = useTranslation(MAIL_NS)
  if (view.state === 'failed') {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-destructive">
          {t('compose.notSaved')} {composeErrorMessage(t, view.error, t('compose.lastGoodKept'), limit)}
        </span>
        <Button type="button" variant="outline" size="xs" onClick={onSave}>
          {t('auth.retry')}
        </Button>
      </div>
    )
  }
  if (sending) {
    return null
  }
  if (view.state === 'saving') {
    return <span className="text-sm text-muted-foreground">{t('compose.saving')}</span>
  }
  if (view.manual) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <span>{t('compose.manualSave')}</span>
        {view.dirty && (
          <Button type="button" variant="outline" size="xs" onClick={onSave}>
            {t('compose.saveDraft')}
          </Button>
        )}
      </div>
    )
  }
  if (view.dirty) {
    return <span className="text-sm text-muted-foreground">{t('compose.saving')}</span>
  }
  return view.ref === null ? null : <span className="text-sm text-muted-foreground">{t('compose.saved')}</span>
}

function Editor({ session, start }: { session: ComposeSession; start: ComposeStart }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const compose = useCompose()
  const { attach, announce, close, finishSent, hide, identified, mailChanged } = compose
  const navigate = useNavigate()
  const [model] = useState(() => new DraftModel(start, mailChanged))
  const view = useSyncExternalStore(model.subscribe, model.getView)
  const { form } = view
  const [typed, setTyped] = useState(NOTHING_TYPED)
  const [copies, setCopies] = useState(start.form.cc.length + start.form.bcc.length > 0)
  const [send, setSend] = useState<SendState>(() => sendStateOf(start.send))
  const [held, setHeld] = useState(() => holdsTheDraft(start.send))
  const [refusalShown, setRefusalShown] = useState<DraftView['refusedBy']>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [dialog, setDialog] = useState<'resend' | 'delete' | 'unsaved' | null>(null)
  const [deleting, setDeleting] = useState(false)
  const unmountSignal = useUnmountSignal()
  const { state: folders } = useAccountFolders(start.account)
  const titleId = useId()
  const subjectId = useId()
  const bodyId = useId()
  const problemId = useId()
  const toInput = useRef<HTMLInputElement>(null)
  const bodyInput = useRef<HTMLTextAreaElement>(null)
  const sending = send.kind === 'sending'
  const spent = send.kind === 'sent'
  const account = compose.account(start.account)
  const replying = start.answers !== null && start.ref === null
  const { attachmentsLost } = view
  const { refusedBy } = view
  // Keep draft only dismisses the banner: the send still holds the draft.
  const readOnly = (held && view.elsewhere) || refusedBy !== null
  // Read-only, not disabled, so text that exists only here stays selectable while it can't change.
  const locked = readOnly || sending || deleting
  const maxRecipients = account?.max_recipients
  const limitWords = maxRecipients === undefined ? null : t('compose.tooManyRecipients', { count: maxRecipients })

  // What the opening read would have said, had this send been there to read.
  if (refusedBy !== refusalShown) {
    setRefusalShown(refusedBy)
    if (refusedBy !== null) {
      setSend(sendStateOf(refusedBy))
    }
  }

  /** The recipient limit in words, when `current` is over it. */
  function overLimit(current: ComposeForm): string | null {
    const count = current.to.length + current.cc.length + current.bcc.length
    return maxRecipients !== undefined && count > maxRecipients ? limitWords : null
  }
  if (problem !== null && problem === limitWords && overLimit(form) === null) {
    setProblem(null)
  }

  useEffect(() => {
    if (replying) {
      bodyInput.current?.focus()
      bodyInput.current?.setSelectionRange(0, 0)
    } else {
      toInput.current?.focus()
    }
  }, [replying])

  useEffect(() => {
    identified(start.composeId, start.account)
  }, [identified, start.composeId, start.account])

  useEffect(
    () =>
      attach({
        ref: () => model.queue.ref,
        leave: () => model.leave(),
        focus: () => {
          bodyInput.current?.focus()
        },
      }),
    [attach, model],
  )

  // The tab closing takes a request that outlives it. Leaving the mail screen, the page lives on.
  useEffect(() => {
    // Strict Mode unmounts once for show, and leaving may have stopped the queue of an empty form.
    model.queue.resume()
    const flush = (): void => {
      model.queue.flushHidden()
    }
    window.addEventListener('pagehide', flush)
    return () => {
      window.removeEventListener('pagehide', flush)
      void model.leave()
    }
  }, [model])

  const failed = view.state === 'failed'
  useEffect(() => {
    if (!failed) {
      return undefined
    }
    const text = t('compose.notSaved')
    announce({ text, urgent: true })
    // A screen reader only hears a change of text, so the next failure must find it gone.
    return () => {
      announce((said) => (said?.text === text ? null : said))
    }
  }, [failed, announce, t])

  useEffect(() => {
    if (attachmentsLost) {
      announce({ text: t('compose.attachmentsLost'), urgent: true })
    }
  }, [attachmentsLost, announce, t])

  useEffect(() => {
    if (refusedBy !== null) {
      announce({ text: t('compose.heldBySend'), urgent: true })
    }
  }, [refusedBy, announce, t])

  const finish = useCallback(
    (end: SendEnd) => {
      const refused = (error: unknown): void => {
        setSend({ kind: 'refused', error })
        announce({ text: composeErrorMessage(t, error, t('compose.notSent'), t('compose.invalidStored')), urgent: true })
      }
      if (end.kind === 'refused' && end.gone) {
        // Busy until it is found: a Send pressed before that would go from the dead ref.
        void model.queue
          .relocate()
          .catch(() => false)
          .then((found) => {
            model.setBusy(false)
            if (found) {
              setSend({ kind: 'relocated' })
              announce({ text: t('compose.changedElsewhere'), urgent: true })
            } else {
              refused(end.error)
            }
          })
        return
      }
      model.setBusy(false)
      switch (end.kind) {
        case 'sent': {
          // Finishing would close the composer on text that was never saved.
          if (model.getView().refusedBy !== null) {
            setSend({ kind: 'sent' })
            announce({ text: t('compose.alreadySent'), urgent: true })
            return
          }
          const { filed, rejected, note_codes } = end.result
          announce({ text: sentMessage(t, { filed, rejected, note_codes }), urgent: false })
          finishSent({ filed, rejected, note_codes })
          return
        }
        case 'already':
          setHeld(true)
          setSend({ kind: 'sent' })
          announce({ text: t('compose.alreadySent'), urgent: true })
          return
        case 'failed':
          setHeld(false)
          model.queue.release()
          setSend({ kind: 'failed', code: end.code })
          announce({
            text: `${t('compose.notSent')} ${end.code === null ? '' : mailCodeMessage(t, end.code)}`,
            urgent: true,
          })
          return
        case 'unknown':
          setHeld(true)
          setSend({ kind: 'unknown' })
          announce({ text: t('compose.unknown'), urgent: true })
          if (end.gone === true) {
            void model.queue.relocate().catch(() => undefined)
          }
          return
        case 'refused':
          refused(end.error)
      }
    },
    [announce, finishSent, model, t],
  )

  // Reopened while its send runs, or found running by a save: it goes on being followed.
  const openedOn = refusedBy ?? start.send
  const running = openedOn?.state === 'sending' ? openedOn.send_id : null
  useEffect(() => {
    if (running === null) {
      return undefined
    }
    model.setBusy(true)
    const controller = new AbortController()
    void followSend(running, controller.signal).then((end) => {
      if (!controller.signal.aborted) {
        finish(end)
      }
    })
    return () => {
      controller.abort()
    }
  }, [running, model, finish])

  /** The text typed in the recipient fields into the draft; false when some of it is no address. */
  function commitRecipients(): boolean {
    const change: Partial<Record<RecipientField, MailAddress[]>> = {}
    const left = { ...typed }
    for (const field of RECIPIENT_FIELDS) {
      const result = commitTyped(form[field], typed[field].text)
      left[field] = { text: result.text, invalid: result.invalid }
      if (result.list.length !== form[field].length) {
        change[field] = result.list
      }
    }
    setTyped(left)
    if (Object.keys(change).length > 0) {
      model.update(change)
    }
    return RECIPIENT_FIELDS.every((field) => !left[field].invalid)
  }

  async function sendDraftNow(confirmResend: boolean): Promise<void> {
    if (model.busy() || deleting || spent || model.getView().refusedBy !== null) {
      return
    }
    setProblem(null)
    if (!commitRecipients()) {
      setCopies((value) => value || typed.cc.text !== '' || typed.bcc.text !== '')
      return
    }
    const { form: current } = model.getView()
    if (current.to.length + current.cc.length + current.bcc.length === 0) {
      setProblem(t('compose.noRecipients'))
      return
    }
    const limit = overLimit(current)
    if (limit !== null) {
      setProblem(limit)
      return
    }
    model.setBusy(true)
    setSend({ kind: 'sending' })
    announce({ text: t('compose.sending'), urgent: false })
    const saved = await model.queue.flush(true)
    const ref = model.queue.ref
    if (!saved || ref === null) {
      const { refusedBy: found } = model.getView()
      // A running send it found has been handed to the effect that follows it, which set this first.
      model.setBusy(found?.state === 'sending')
      setSend(sendStateOf(found))
      // A failed save says so itself. Anything else would otherwise look like a click that did nothing.
      if (model.queue.state !== 'failed') {
        setProblem(t('compose.stillSaving'))
      }
      return
    }
    const signal = unmountSignal()
    const end = await runSend(ref, confirmResend, signal)
    if (!signal.aborted) {
      finish(end)
    }
  }

  async function closeEditor(): Promise<void> {
    if (model.busy() || deleting) {
      return
    }
    commitRecipients()
    if (await model.leave()) {
      close()
    } else if (!model.busy()) {
      setDialog('unsaved')
    }
  }

  async function deleteNow(): Promise<void> {
    setDialog(null)
    setDeleting(true)
    const ref = await model.queue.stop()
    try {
      if (ref !== null) {
        await deleteDraft(ref)
      }
      close()
    } catch (error) {
      if (error instanceof ApiRequestError && error.code === 'not_found') {
        close()
        return
      }
      model.queue.resume()
      setDeleting(false)
      setProblem(mailErrorMessage(t, error, t('compose.deleteFailed')))
    }
  }

  function openSent(path: string): void {
    hide()
    void navigate({ to: '/mail', search: { account: start.account, folder: path } })
  }

  const sentFolder = roleFolder(loadedFolders(folders), 'sent')
  const title = t(TITLES[session.source.kind])
  const typedRecipient = (field: RecipientField, label: string, ref?: typeof toInput): ReactElement => (
    <RecipientInput
      ref={ref}
      label={label}
      value={form[field]}
      typed={typed[field]}
      onChange={(value) => {
        model.update({ [field]: value })
      }}
      onTyped={(value) => {
        setTyped((previous) => ({ ...previous, [field]: value }))
      }}
    />
  )

  function addFiles(files: File[]): void {
    const added = files.map((file) => ({ kind: 'file' as const, id: crypto.randomUUID(), file }))
    model.update({ tray: [...model.getView().form.tray, ...added] })
  }

  function removeItem(item: TrayItem): void {
    model.update({ tray: model.getView().form.tray.filter((each) => each.id !== item.id) })
  }

  return (
    <form
      aria-labelledby={titleId}
      className="flex flex-1 flex-col gap-3"
      onSubmit={(event) => {
        // Enter in a field never sends: only the Send button does.
        event.preventDefault()
      }}
    >
      <header className="flex items-center gap-2">
        <h2 id={titleId} className="min-w-0 flex-1 truncate text-lg font-semibold tracking-tight">
          {title}
        </h2>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={t('compose.close')}
          aria-disabled={sending}
          onClick={() => void closeEditor()}
        >
          <X aria-hidden="true" />
        </Button>
      </header>
      {start.lossy && <p className="rounded-lg border p-2 text-sm">{t('compose.lossy')}</p>}
      {attachmentsLost && <p className="rounded-lg border p-2 text-sm">{t('compose.attachmentsLost')}</p>}
      {readOnly && <p className="rounded-lg border p-2 text-sm">{t('compose.heldBySend')}</p>}
      <fieldset className="flex min-w-0 flex-1 flex-col gap-3">
        <legend className="sr-only">{title}</legend>
        <fieldset disabled={locked} className="contents">
          <IdentitySelect
            account={account}
            value={form.from}
            onChange={(address) => {
              model.update({ from: address })
            }}
          />
          {typedRecipient('to', t('message.to'), toInput)}
          {copies ? (
            <>
              {typedRecipient('cc', t('message.cc'))}
              {typedRecipient('bcc', t('compose.bcc'))}
            </>
          ) : (
            <Button
              type="button"
              variant="ghost"
              size="xs"
              className="self-start"
              onClick={() => {
                setCopies(true)
              }}
            >
              {t('compose.showCopies')}
            </Button>
          )}
        </fieldset>
        <div className="flex flex-col gap-1">
          <label htmlFor={subjectId} className="text-sm text-muted-foreground">
            {t('compose.subject')}
          </label>
          <Input
            id={subjectId}
            type="text"
            maxLength={MAX_SUBJECT}
            readOnly={locked}
            value={form.subject}
            onChange={(event) => {
              model.update({ subject: event.target.value })
            }}
          />
        </div>
        <div className="flex min-h-48 flex-1 flex-col gap-1">
          <label htmlFor={bodyId} className="text-sm text-muted-foreground">
            {t('compose.body')}
          </label>
          <Textarea
            ref={bodyInput}
            id={bodyId}
            className="flex-1"
            maxLength={MAX_TEXT}
            readOnly={locked}
            value={form.text}
            onChange={(event) => {
              model.update({ text: event.target.value })
            }}
          />
        </div>
        <fieldset disabled={locked} className="contents">
          <AttachmentTray
            items={form.tray}
            maxMb={account?.attachment_max_mb ?? DEFAULT_ATTACHMENT_MB}
            onAdd={addFiles}
            onRemove={removeItem}
          />
        </fieldset>
      </fieldset>
      <SendStatus
        state={send}
        onOpenSent={
          sentFolder === undefined
            ? null
            : () => {
                openSent(sentFolder.path)
              }
        }
        onKeep={() => {
          setSend({ kind: 'idle' })
        }}
        onSendAnyway={
          refusedBy !== null
            ? null
            : () => {
                setDialog('resend')
              }
        }
      />
      {problem !== null && (
        <p id={problemId} role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
      <footer className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          aria-disabled={sending || deleting || spent || refusedBy !== null}
          aria-describedby={problem === null ? undefined : problemId}
          onClick={() => void sendDraftNow(false)}
        >
          {sending ? t('compose.sending') : t('compose.send')}
        </Button>
        {(view.ref !== null || !isBlank(form)) && (
          <Button
            type="button"
            variant="ghost"
            aria-disabled={sending || deleting}
            onClick={() => {
              if (!model.busy() && !deleting) {
                setDialog('delete')
              }
            }}
          >
            {t('compose.deleteDraft')}
          </Button>
        )}
        {refusedBy !== null && <span className="text-sm text-destructive">{t('compose.notSaved')}</span>}
        {!readOnly && (
          <SaveStatus view={view} sending={sending} limit={overLimit(form)} onSave={() => void model.queue.flush()} />
        )}
      </footer>
      {dialog === 'resend' && (
        <Dialog
          label={t('compose.sendAnyway')}
          onCancel={() => {
            setDialog(null)
          }}
        >
          <p className="text-sm">{t('compose.sendAnywayQuestion')}</p>
          <div className="mt-4 flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setDialog(null)
              }}
            >
              {t('compose.cancel')}
            </Button>
            <Button
              type="button"
              variant="destructive"
              size="sm"
              onClick={() => {
                setDialog(null)
                void sendDraftNow(true)
              }}
            >
              {t('compose.sendAnyway')}
            </Button>
          </div>
        </Dialog>
      )}
      {dialog === 'delete' && (
        <Dialog
          label={t('compose.deleteDraft')}
          onCancel={() => {
            setDialog(null)
          }}
        >
          <p className="text-sm">{t('compose.deleteQuestion')}</p>
          <div className="mt-4 flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setDialog(null)
              }}
            >
              {t('compose.cancel')}
            </Button>
            <Button type="button" variant="destructive" size="sm" onClick={() => void deleteNow()}>
              {t('compose.deleteDraft')}
            </Button>
          </div>
        </Dialog>
      )}
      {dialog === 'unsaved' && (
        <Dialog
          label={t('compose.notSaved')}
          onCancel={() => {
            setDialog(null)
          }}
        >
          <p className="text-sm">{t('compose.unsavedQuestion')}</p>
          <div className="mt-4 flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setDialog(null)
              }}
            >
              {t('compose.keepEditing')}
            </Button>
            <Button type="button" variant="destructive" size="sm" onClick={close}>
              {t('compose.closeAnyway')}
            </Button>
          </div>
        </Dialog>
      )}
    </form>
  )
}

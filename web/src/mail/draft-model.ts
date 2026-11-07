import type { Account } from '@/api/accounts.ts'
import { ApiRequestError } from '@/api/http.ts'
import { deleteDraft, findDraft, prefillDraft, readDraft, saveDraft, type Draft, type DraftSend } from '@/api/mail.ts'

import { isBlank, isFile, isPart, reconcileTray, trayBytes, trayPart, type ComposeForm } from './compose-form.ts'
import type { ComposeSession } from './compose-session.ts'
import { SaveQueue, type SaveState } from './save-queue.ts'

/** What an editor opens on. */
export interface ComposeStart {
  account: string
  composeId: string
  ref: string | null
  answers: string | null
  /** The draft has no `compose_id`: another client wrote it, so its ref is its send's key. */
  elsewhere: boolean
  form: ComposeForm
  lossy: boolean
  send: Draft['send']
}

const BLANK_FORM: ComposeForm = { from: null, to: [], cc: [], bcc: [], subject: '', text: '', tray: [] }

/** An identity of the account, or `null` (the default) when `from` is none of them. */
function knownFrom(account: Account | undefined, from: string | null): string | null {
  return account?.identities.some((identity) => identity.address === from) === true ? from : null
}

function draftStart(draft: Draft, composeId: string, account: Account | undefined): ComposeStart {
  return {
    account: draft.account,
    composeId: draft.compose_id ?? composeId,
    ref: draft.ref,
    answers: draft.answers,
    elsewhere: draft.compose_id === null,
    form: {
      from: knownFrom(account, draft.from),
      to: draft.to,
      cc: draft.cc,
      bcc: draft.bcc,
      subject: draft.subject,
      text: draft.text,
      tray: draft.parts.map((part) => trayPart({ ...part, ref: draft.ref })),
    },
    lossy: draft.lossy,
    send: draft.send,
  }
}

/** Reads what `session` starts from; `null` for a recovered draft that was never saved. */
export async function loadStart(
  session: ComposeSession,
  accountOf: (id: string) => Account | undefined,
  signal: AbortSignal,
): Promise<ComposeStart | null> {
  const { source, composeId } = session
  switch (source.kind) {
    case 'new':
      return { account: source.account, composeId, ref: null, answers: null, elsewhere: false, form: BLANK_FORM, lossy: false, send: null }
    case 'draft': {
      const draft = await readDraft(source.ref, signal)
      return draftStart(draft, composeId, accountOf(draft.account))
    }
    case 'recover': {
      const found = await findDraft(source.account, source.composeId, signal)
      if (found === null) {
        return null
      }
      return draftStart(await readDraft(found.ref, signal), source.composeId, accountOf(source.account))
    }
    default: {
      const prefill = await prefillDraft(source.ref, source.kind, signal)
      return {
        account: prefill.account,
        composeId,
        ref: null,
        answers: prefill.answers,
        elsewhere: false,
        form: {
          from: knownFrom(accountOf(prefill.account), prefill.from),
          to: prefill.to,
          cc: prefill.cc,
          bcc: prefill.bcc,
          subject: prefill.subject,
          text: prefill.text,
          tray: prefill.attachments.map(trayPart),
        },
        lossy: false,
        send: null,
      }
    }
  }
}

export interface DraftView {
  form: ComposeForm
  ref: string | null
  state: SaveState
  error: unknown
  dirty: boolean
  /** Saved only on request. */
  manual: boolean
  /** The draft went from the server, and the attachments it held with it. */
  attachmentsLost: boolean
  /** The version held is the one another client wrote; a save here makes it an own draft. */
  elsewhere: boolean
  /** The send the server refused the form's edits for: what is on screen is not what is stored. */
  refusedBy: DraftSend | null
}

/** One open draft: the form as typed and the saves that follow it. */
export class DraftModel {
  readonly queue: SaveQueue<ComposeForm>
  private form: ComposeForm
  private view: DraftView
  private readonly listeners = new Set<() => void>()
  private sending = false
  private attachmentsLost = false
  private readonly writtenElsewhere: string | null

  constructor(start: ComposeStart, onSaved: () => void) {
    this.form = start.form
    this.writtenElsewhere = start.elsewhere ? start.ref : null
    this.queue = new SaveQueue<ComposeForm>(
      {
        snapshot: (textOnly) => (textOnly ? { ...this.form, tray: this.form.tray.filter(isPart) } : this.form),
        bytes: () => trayBytes(this.form.tray),
        save: (form, ref, keepalive) =>
          saveDraft(
            {
              account: start.account,
              compose_id: start.composeId,
              ref,
              answers: start.answers,
              from: form.from,
              to: form.to,
              cc: form.cc,
              bcc: form.bcc,
              subject: form.subject,
              text: form.text,
              keep: form.tray.filter(isPart).map(({ ref: from, part }) => ({ ref: from, part })),
            },
            form.tray.filter(isFile).map((item) => item.file),
            { keepalive },
          ),
        find: () => findDraft(start.account, start.composeId),
        landed: (form, ref, parts) => {
          const tray = reconcileTray(this.form.tray, form?.tray ?? this.form.tray.filter(isPart), ref, parts)
          this.form = { ...this.form, tray }
          onSaved()
          this.refresh()
          // Files a text-only save left behind still have to go.
          if (tray.some(isFile)) {
            this.queue.touch()
          }
        },
        // A save naming a part of the dead ref would be refused, and so would every one after it.
        lost: (ref) => {
          const tray = this.form.tray.filter((item) => !isPart(item) || item.ref !== ref)
          this.attachmentsLost ||= tray.length !== this.form.tray.length
          this.form = { ...this.form, tray }
          this.refresh()
        },
        changed: () => {
          this.refresh()
        },
      },
      start.ref,
    )
    this.view = this.currentView()
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  getView = (): DraftView => this.view

  update(change: Partial<ComposeForm>): void {
    this.form = { ...this.form, ...change }
    this.queue.touch()
  }

  /** A send is under way: the draft cannot be left or changed. */
  busy(): boolean {
    return this.sending
  }

  setBusy(busy: boolean): void {
    this.sending = busy
  }

  /** Saves what is unsaved, to stop editing; false when the draft must stay open. */
  async leave(): Promise<boolean> {
    if (this.sending) {
      return false
    }
    if (this.queue.ref === null && isBlank(this.form)) {
      return this.discard()
    }
    // A send pressed during the wait owns the draft now, and has to be seen to its end.
    return (await this.queue.flush()) && !this.sending
  }

  /** Nothing to keep, though a timer may be armed and a first save on its way. */
  private async discard(): Promise<boolean> {
    const ref = await this.queue.stop()
    if (!isBlank(this.form)) {
      this.queue.resume()
      return this.leave()
    }
    if (ref === null) {
      return true
    }
    try {
      await deleteDraft(ref)
      return true
    } catch (error) {
      if (error instanceof ApiRequestError && error.code === 'not_found') {
        return true
      }
      this.queue.resume()
      return false
    }
  }

  private currentView(): DraftView {
    const { ref, state, error, dirty, manual, holding } = this.queue
    const elsewhere = ref !== null && ref === this.writtenElsewhere
    return {
      form: this.form,
      ref,
      state,
      error,
      dirty,
      manual,
      attachmentsLost: this.attachmentsLost,
      elsewhere,
      refusedBy: holding,
    }
  }

  private refresh(): void {
    this.view = this.currentView()
    for (const listener of this.listeners) {
      listener()
    }
  }
}

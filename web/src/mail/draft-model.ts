import { accountSignatures, type AccountId, type Account, type IdentitySignature } from '@/api/accounts.ts'
import { ApiRequestError } from '@/api/http.ts'
import {
  cancelScheduled,
  deleteDraft,
  findDraft,
  importDraft,
  keepDesigned,
  prefillDraft,
  readDraft,
  saveDraft,
  type Draft,
  type DraftSend,
  type ImportedDraft,
  type ImportFields,
  type ImportSource,
  type ScheduledSend,
} from '@/api/mail.ts'

import {
  inlinePart,
  isBlank,
  isFile,
  isPart,
  reconcileInline,
  reconcileTray,
  shownImages,
  trayBytes,
  trayPart,
  type ComposeForm,
} from './compose-form.ts'
import type { ComposeSession } from './compose-session.ts'
import { SaveQueue, type SaveState } from './save-queue.ts'
import { fromAddress, newMessageHtml, signatureOf } from './signature.ts'

/** What an editor opens on. */
export interface ComposeStart {
  account: AccountId
  composeId: string
  ref: string | null
  answers: string | null
  forwards: string | null
  /** The draft has no `compose_id`: another client wrote it, so its ref is its send's key. */
  elsewhere: boolean
  form: ComposeForm
  lossy: boolean
  /** Imported as built: its body is not edited here, and a save keeps it as it is. */
  designed: boolean
  send: Draft['send']
  /** Its held send still to go, opened from Drafts: the first change cancels it. */
  scheduled: ScheduledSend | null
  /** The account's identities with their signatures, to swap one for another when From changes. */
  signatures: IdentitySignature[]
}

const BLANK_FORM: ComposeForm = { from: null, to: [], cc: [], bcc: [], subject: '', text: '', html: '', inline: [], tray: [] }

/** An identity of the account, or `null` (the default) when `from` is none of them. */
function knownFrom(account: Account | undefined, from: string | null): string | null {
  return account?.identities.some((identity) => identity.address === from) === true ? from : null
}

function draftStart(draft: Draft, composeId: string, account: Account | undefined, signatures: IdentitySignature[]): ComposeStart {
  return {
    account: draft.account,
    composeId: draft.compose_id ?? composeId,
    ref: draft.ref,
    answers: draft.answers,
    forwards: draft.forwards,
    elsewhere: draft.compose_id === null,
    form: {
      from: knownFrom(account, draft.from),
      to: draft.to,
      cc: draft.cc,
      bcc: draft.bcc,
      subject: draft.subject,
      text: draft.text,
      html: draft.html,
      inline: draft.inline.map((part) => inlinePart({ ...part, ref: draft.ref })),
      tray: draft.parts.map((part) => trayPart({ ...part, ref: draft.ref })),
    },
    lossy: draft.lossy,
    designed: draft.designed,
    send: draft.send,
    scheduled: draft.scheduled,
    signatures,
  }
}

/** Reads what `session` starts from; `null` for a recovered draft that was never saved. */
export async function loadStart(
  session: ComposeSession,
  accountOf: (id: AccountId) => Account | undefined,
  signal: AbortSignal,
): Promise<ComposeStart | null> {
  const { source, composeId } = session
  // The signature a new message starts with, and its swap when From changes: without them, the
  // composer still opens, with none.
  const signaturesOf = (account: AccountId): Promise<IdentitySignature[]> =>
    accountSignatures(account, signal).catch((error: unknown) => {
      if (signal.aborted) {
        throw error
      }
      return []
    })
  switch (source.kind) {
    case 'new': {
      const signatures = await signaturesOf(source.account)
      const signature = signatureOf(signatures, fromAddress(accountOf(source.account), null))
      return {
        account: source.account,
        composeId,
        ref: null,
        answers: null,
        forwards: null,
        elsewhere: false,
        form: { ...BLANK_FORM, html: newMessageHtml(signature) },
        lossy: false,
        designed: false,
        send: null,
        scheduled: null,
        signatures,
      }
    }
    case 'draft': {
      // A ref is opaque here: the account is known once the draft is read.
      const draft = await readDraft(source.ref, signal)
      return draftStart(draft, composeId, accountOf(draft.account), await signaturesOf(draft.account))
    }
    case 'recover': {
      const [found, signatures] = await Promise.all([findDraft(source.account, source.composeId, signal), signaturesOf(source.account)])
      if (found === null) {
        return null
      }
      return draftStart(await readDraft(found.ref, signal), source.composeId, accountOf(source.account), signatures)
    }
    default: {
      const [prefill, guessed] = await Promise.all([
        prefillDraft(source.ref, source.kind, source.quote ?? null, signal),
        signaturesOf(source.account),
      ])
      const signatures = prefill.account === source.account ? guessed : await signaturesOf(prefill.account)
      return {
        account: prefill.account,
        composeId,
        ref: null,
        answers: prefill.answers,
        forwards: prefill.forwards,
        elsewhere: false,
        form: {
          from: knownFrom(accountOf(prefill.account), prefill.from),
          to: prefill.to,
          cc: prefill.cc,
          bcc: prefill.bcc,
          subject: prefill.subject,
          text: prefill.text,
          html: prefill.html,
          inline: prefill.inline.map(inlinePart),
          tray: prefill.attachments.map(trayPart),
        },
        lossy: false,
        designed: false,
        send: null,
        scheduled: null,
        signatures,
      }
    }
  }
}

/**
 * The held send of a draft opened from Drafts: `held` until the user's first change, which cancels
 * it before anything is saved (`cancelling`), leaving a plain draft (`cancelled`); `taken` when
 * the worker had it already, so nothing here is saved over it; `cancelFailed` when the cancel did
 * not get through, the change kept unsaved until the next one tries again.
 */
export interface HeldSchedule {
  state: 'held' | 'cancelling' | 'cancelled' | 'taken' | 'cancelFailed'
  sendAt: string
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
  schedule: HeldSchedule | null
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
  private readonly scheduledId: string | null
  private schedule: HeldSchedule | null
  private cancelling: Promise<void> | null = null
  private readonly start: ComposeStart

  constructor(start: ComposeStart, onSaved: () => void) {
    this.start = start
    this.form = start.form
    this.writtenElsewhere = start.elsewhere ? start.ref : null
    const held = start.scheduled
    this.scheduledId = held?.scheduled_id ?? null
    this.schedule = held === null ? null : { state: held.state === 'held' ? 'held' : 'taken', sendAt: held.send_at }
    this.queue = new SaveQueue<ComposeForm>(
      {
        snapshot: (textOnly) => {
          const inline = shownImages(this.form.html, this.form.inline)
          return textOnly
            ? { ...this.form, inline: inline.filter(isPart), tray: this.form.tray.filter(isPart) }
            : { ...this.form, inline }
        },
        bytes: () => trayBytes([...this.form.tray, ...shownImages(this.form.html, this.form.inline)]),
        save: (form, ref, keepalive) => {
          if (start.designed && ref !== null) {
            return keepDesigned({ ...this.importFields(form, ref), ref }, { keepalive })
          }
          const uploads = form.inline.filter(isFile)
          return saveDraft(
            {
              account: start.account,
              compose_id: start.composeId,
              ref,
              answers: start.answers,
              forwards: start.forwards,
              from: form.from,
              to: form.to,
              cc: form.cc,
              bcc: form.bcc,
              subject: form.subject,
              text: form.html === null ? form.text : '',
              html: form.html,
              content_ids: uploads.map((item) => item.contentId),
              keep: [
                ...form.inline.filter(isPart).map(({ ref: from, part }) => ({ ref: from, part, inline: true })),
                ...form.tray.filter(isPart).map(({ ref: from, part }) => ({ ref: from, part })),
              ],
            },
            [...uploads, ...form.tray.filter(isFile)].map((item) => item.file),
            { keepalive },
          )
        },
        find: () => findDraft(start.account, start.composeId),
        landed: (form, ref, { parts, inline: shown }) => {
          // A designed draft's files are its own, never changed here: what it holds is what it shows.
          const tray = start.designed
            ? parts.map((part) => trayPart({ ...part, ref }))
            : reconcileTray(this.form.tray, form?.tray ?? this.form.tray.filter(isPart), ref, parts)
          const inline = reconcileInline(this.form.inline, ref, shown, start.forwards)
          this.form = { ...this.form, tray, inline }
          onSaved()
          this.refresh()
          // Files a text-only save left behind still have to go.
          if (tray.some(isFile) || shownImages(this.form.html, inline).some(isFile)) {
            this.queue.touch()
          }
        },
        // A save naming a part of the dead ref would be refused, and so would every one after it.
        lost: (ref) => {
          const tray = this.form.tray.filter((item) => !isPart(item) || item.ref !== ref)
          const inline = this.form.inline.filter((item) => !isPart(item) || item.ref !== ref)
          this.attachmentsLost ||= tray.length !== this.form.tray.length || inline.length !== this.form.inline.length
          this.form = { ...this.form, tray, inline }
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

  /** An import's fields from `form`: everything as the composer holds it. */
  private importFields(form: ComposeForm, ref: string | null): ImportFields {
    const { account, composeId } = this.start
    return { account, compose_id: composeId, ref, from: form.from, to: form.to, cc: form.cc, bcc: form.bcc, subject: form.subject }
  }

  /**
   * Imports `source` in place of the designed draft, under the From, recipients and subject the
   * composer holds; saves wait meanwhile. A refusal leaves the draft as it was.
   */
  async replace(source: ImportSource): Promise<ImportedDraft> {
    const ref = await this.queue.stop()
    try {
      const imported = await importDraft(this.importFields(this.form, ref), source)
      await this.queue.took(imported)
      return imported
    } finally {
      this.queue.resume()
    }
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
    const state = this.schedule?.state
    if (state === undefined || state === 'cancelled') {
      this.queue.touch()
      return
    }
    if (state === 'held' || state === 'cancelFailed') {
      this.cancelling = this.cancelHeld()
    }
    this.refresh()
  }

  /** The held send cancelled before the first save, which follows only once it is. */
  private async cancelHeld(): Promise<void> {
    const { schedule, scheduledId } = this
    if (schedule === null || scheduledId === null) {
      return
    }
    this.schedule = { ...schedule, state: 'cancelling' }
    let state: HeldSchedule['state'] = 'cancelled'
    try {
      await cancelScheduled(scheduledId)
    } catch (error) {
      // Gone already (cancelled elsewhere, or sent, when the server refuses the save itself).
      if (!(error instanceof ApiRequestError && error.code === 'not_found')) {
        state = error instanceof ApiRequestError && error.code === 'send_started' ? 'taken' : 'cancelFailed'
      }
    }
    this.schedule = { ...schedule, state }
    this.cancelling = null
    if (state === 'cancelled') {
      this.queue.touch()
    } else {
      this.refresh()
    }
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
    // Awaited only when there is one: Strict Mode's trial unmount must stop the queue at once.
    if (this.cancelling !== null) {
      await this.cancelling
    }
    // Changes the held send's cancel kept from being saved would be lost.
    if (this.schedule?.state === 'cancelFailed') {
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
      schedule: this.schedule,
    }
  }

  private refresh(): void {
    this.view = this.currentView()
    for (const listener of this.listeners) {
      listener()
    }
  }
}

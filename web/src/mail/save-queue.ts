import { ApiRequestError } from '@/api/http.ts'
import { holdingSend, type DraftSend, type FoundDraft, type MessagePart, type SavedDraft } from '@/api/mail.ts'

const BASE_DEBOUNCE_MS = 2_000
/** Each save rewrites the whole message, attachments included. */
const DEBOUNCE_MS_PER_MIB = 1_500
const MAX_DEBOUNCE_MS = 60_000
/** Someone typing without a pause still saves, after this many debounces. */
const MAX_WAIT_FACTOR = 5
/** A save answered without a ref may not be searchable at once. */
const FIND_DELAYS_MS = [500, 1_000, 2_000]

export function debounceFor(bytes: number): number {
  return Math.min(MAX_DEBOUNCE_MS, BASE_DEBOUNCE_MS + (bytes / 2 ** 20) * DEBOUNCE_MS_PER_MIB)
}

export type SaveState = 'idle' | 'saving' | 'saved' | 'failed'

export interface SaveHooks<S> {
  /** What a save carries, read when it starts; `textOnly` is for a request that outlives the page. */
  snapshot: (textOnly: boolean) => S
  /** The size of the draft now, in bytes. */
  bytes: () => number
  save: (snapshot: S, ref: string | null, keepalive: boolean) => Promise<SavedDraft>
  find: () => Promise<FoundDraft | null>
  /** A version is the latest: `snapshot` is what it holds, `null` for one this client did not write. */
  landed: (snapshot: S | null, ref: string, parts: MessagePart[]) => void
  /** The version `ref` is gone, and no other took its place. */
  lost: (ref: string) => void
  /** State to show changed. */
  changed: () => void
}

/** A save the server may or may not have stored. */
interface Unsettled<S> {
  snapshot: S
  version: number
  /**
   * The server said it stored it, without saying where. The version it replaced stays, and a
   * search may see only that one, so nothing but a different ref settles it.
   */
  answered: boolean
}

/** The server answered and stored nothing; a 5xx (a proxy's timeout) says neither. */
function refused(error: unknown): boolean {
  return error instanceof ApiRequestError && error.status < 500
}

/** The ref was the draft and no longer is. */
export function draftGone(error: unknown): boolean {
  return error instanceof ApiRequestError && (error.code === 'not_found' || error.code === 'stale_reference')
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * The saves of one draft (`docs/requirements/mail-client.md`, Drafts). One runs at a time, always
 * from the latest ref: an IMAP save replaces a message, so a save from an older ref would leave
 * two. Before any further save, one whose answer was lost is looked for by its `compose_id`.
 * A failure changes nothing here: the last good version stays the draft.
 */
export class SaveQueue<S> {
  ref: string | null
  state: SaveState
  /** Saves only when asked: the server would otherwise fill Trash with versions. */
  manual = false
  error: unknown = null
  /** The send a save was refused for: the server takes no version past it, until `release`. */
  holding: DraftSend | null = null

  private readonly hooks: SaveHooks<S>
  private version = 0
  private savedVersion = 0
  private attemptedVersion = 0
  private running: Promise<boolean> | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private dirtySince: number | null = null
  private unsettled: Unsettled<S> | null = null
  /**
   * The version held was found after a lost answer, and only a save's own answer says what a
   * version holds: it may be another writer's. Enough to stop writing twice, not to send.
   */
  private unverified = false
  private held = false

  constructor(hooks: SaveHooks<S>, ref: string | null) {
    this.hooks = hooks
    this.ref = ref
    this.state = ref === null ? 'idle' : 'saved'
  }

  get dirty(): boolean {
    return this.version !== this.savedVersion
  }

  /** The draft changed: save it once the user pauses. */
  touch(): void {
    this.version += 1
    this.dirtySince ??= Date.now()
    this.schedule()
    this.hooks.changed()
  }

  /**
   * Saves what is unsaved now; true when all of it is stored. `needRef` is for a send: it also
   * saves a draft that was never saved, and is true only once the ref held is this very version.
   */
  async flush(needRef = false): Promise<boolean> {
    while (this.running !== null) {
      await this.running
    }
    this.clearTimer()
    if (this.held || this.holding !== null) {
      return false
    }
    if (!this.dirty && this.unsettled === null && !(needRef && (this.ref === null || this.unverified))) {
      if (this.state === 'failed') {
        this.set('saved')
      }
      return true
    }
    return this.start(false, needRef)
  }

  /**
   * The page is going away: what can still be saved goes in a request that outlives it. Not while
   * a save runs: a second one could only race it, and that one's end schedules any further save.
   */
  flushHidden(): void {
    if (this.running === null && this.dirty && !this.held && this.holding === null) {
      this.clearTimer()
      void this.start(true, false)
    }
  }

  /** No more saves until `resume`; the ref of the latest version, once nothing is on its way. */
  async stop(): Promise<string | null> {
    this.held = true
    this.clearTimer()
    while (this.running !== null) {
      await this.running
    }
    try {
      await this.settle()
    } catch {
      // Still unknown: the ref known last is what there is to act on.
    }
    return this.ref
  }

  resume(): void {
    this.held = false
    if (this.dirty) {
      this.schedule()
    }
  }

  /** The send that held the draft failed, so a save can go through again. */
  release(): void {
    if (this.holding !== null) {
      this.holding = null
      this.set('saved')
      if (this.dirty) {
        this.schedule()
      }
    }
  }

  /**
   * The ref held turned out not to be the draft any more. Whoever wrote what is there now, the
   * form is what the user sees, so it is what gets written and sent. True when a version is there.
   */
  async relocate(): Promise<boolean> {
    while (this.running !== null) {
      await this.running
    }
    const found = await this.hooks.find()
    this.adopt(found)
    this.touch()
    return found !== null
  }

  private set(state: SaveState, error: unknown = null): void {
    this.state = state
    this.error = error
    this.hooks.changed()
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer)
      this.timer = null
    }
  }

  private schedule(): void {
    if (this.manual || this.held || this.holding !== null || this.running !== null) {
      return
    }
    this.clearTimer()
    const debounce = debounceFor(this.hooks.bytes())
    const left = (this.dirtySince ?? Date.now()) + debounce * MAX_WAIT_FACTOR - Date.now()
    this.timer = setTimeout(
      () => {
        this.timer = null
        void this.start(false, false)
      },
      Math.max(0, Math.min(debounce, left)),
    )
  }

  private start(keepalive: boolean, needRef: boolean): Promise<boolean> {
    const run = this.attempt(keepalive, needRef).finally(() => {
      this.running = null
      // An edit made during a failed save gets its own try; without one, it waits for the retry.
      if (this.dirty && (this.state !== 'failed' || this.version !== this.attemptedVersion)) {
        this.schedule()
      }
      this.hooks.changed()
    })
    this.running = run
    return run
  }

  private async attempt(keepalive: boolean, needRef: boolean): Promise<boolean> {
    this.set('saving')
    this.dirtySince = null
    this.attemptedVersion = this.version
    try {
      await this.settle()
      if (this.dirty || (needRef && (this.unverified || (this.ref === null && this.unsettled === null)))) {
        await this.write(keepalive, true)
      }
      // That save may have lost its answer too, and been found in its turn.
      if (needRef && this.unverified) {
        await this.write(keepalive, true)
      }
      this.set('saved')
      return !this.dirty && !(needRef && (this.unsettled !== null || this.unverified))
    } catch (error) {
      this.holding = holdingSend(error)
      this.set('failed', error)
      return false
    }
  }

  private async write(keepalive: boolean, mayRetry: boolean): Promise<void> {
    const version = this.version
    const snapshot = this.hooks.snapshot(keepalive)
    const ref = this.ref
    let answer: SavedDraft
    try {
      answer = await this.hooks.save(snapshot, ref, keepalive)
    } catch (error) {
      if (!refused(error)) {
        this.unsettled = { snapshot, version, answered: false }
        await this.settleQuietly()
        if (this.savedVersion < version) {
          throw error
        }
        return
      }
      if (mayRetry && ref !== null && draftGone(error)) {
        this.adopt(await this.hooks.find())
        if (this.ref !== ref) {
          await this.write(keepalive, false)
          return
        }
      }
      throw error
    }
    this.manual = !answer.autosave
    this.savedVersion = version
    this.unverified = false
    if (answer.ref === null) {
      this.unsettled = { snapshot, version, answered: true }
      for (const delay of FIND_DELAYS_MS) {
        await wait(delay)
        if (await this.settleQuietly()) {
          return
        }
      }
      return
    }
    this.ref = answer.ref
    this.unsettled = null
    this.hooks.landed(snapshot, answer.ref, answer.parts)
  }

  /** What the server holds under this `compose_id` is the draft; nothing there, it is gone. */
  private adopt(found: FoundDraft | null): void {
    const before = this.ref
    this.ref = found?.ref ?? null
    if (found !== null) {
      this.hooks.landed(null, found.ref, found.parts)
    } else if (before !== null) {
      this.hooks.lost(before)
    }
  }

  /** True when the save is accounted for; else the next attempt starts by asking again. */
  private async settleQuietly(): Promise<boolean> {
    try {
      await this.settle()
    } catch {
      return false
    }
    return this.unsettled === null
  }

  /** Finds out where a save without a usable answer went. */
  private async settle(): Promise<void> {
    const pending = this.unsettled
    if (pending === null) {
      return
    }
    const found = await this.hooks.find()
    if (pending.answered && (found === null || found.ref === this.ref)) {
      return
    }
    this.unsettled = null
    if (found === null || found.ref === this.ref) {
      return
    }
    this.ref = found.ref
    this.savedVersion = Math.max(this.savedVersion, pending.version)
    this.unverified = !pending.answered
    this.hooks.landed(pending.snapshot, found.ref, found.parts)
  }
}

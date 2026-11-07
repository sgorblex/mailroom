import { ApiRequestError } from '@/api/http.ts'
import { readSend, sendDraft, type SendResult } from '@/api/mail.ts'

import { draftGone } from './save-queue.ts'

const RETRY_MS = 1_000
const POLL_MS = [1_000, 1_000, 2_000, 3_000]
/** Past the server's 360 s ceiling, by when a send can only read as finished. */
const GIVE_UP_MS = 400_000
/** Past the two 30 s calls after delivery, by when a `sent` send is `settled`; a restart can leave it false. */
const SETTLE_MS = 70_000

export type SendEnd =
  | { kind: 'sent'; result: SendResult }
  /** An earlier delivery spent the key: this one sent nothing, whatever the draft holds now. */
  | { kind: 'already' }
  | { kind: 'failed'; code: string | null }
  /** `gone`: the draft's ref no longer holds, so the next attempt must save it again. */
  | { kind: 'unknown'; gone?: boolean }
  /** Refused before anything was claimed. */
  | { kind: 'refused'; error: unknown; gone: boolean }

function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms)
    signal.addEventListener('abort', () => {
      clearTimeout(timer)
      resolve()
    })
  })
}

function ending(result: SendResult): SendEnd | null {
  switch (result.state) {
    case 'sending':
      return null
    case 'sent':
      return result.settled ? { kind: 'sent', result } : null
    case 'failed':
      return { kind: 'failed', code: result.code }
    case 'unknown':
      return { kind: 'unknown' }
  }
}

/**
 * Follows a send until it ends, which for a delivered one is once it is `settled`. A lost
 * connection here says nothing about the send itself, and a delivered send stays delivered.
 */
export async function followSend(sendId: string, signal: AbortSignal, first?: SendResult): Promise<SendEnd> {
  let delivered = first?.state === 'sent' ? first : null
  let deadline = Date.now() + (delivered === null ? GIVE_UP_MS : SETTLE_MS)
  for (let poll = 0; Date.now() < deadline && !signal.aborted; poll += 1) {
    await wait(POLL_MS[Math.min(poll, POLL_MS.length - 1)] ?? RETRY_MS, signal)
    try {
      const result = await readSend(sendId, signal)
      const end = ending(result)
      if (end !== null) {
        return end
      }
      if (result.state === 'sent') {
        deadline = delivered === null ? Date.now() + SETTLE_MS : deadline
        delivered = result
      }
    } catch (error) {
      if (error instanceof ApiRequestError && error.status < 500) {
        break
      }
    }
  }
  return delivered === null ? { kind: 'unknown' } : { kind: 'sent', result: delivered }
}

/**
 * Sends the draft `ref`. The send is keyed by the draft's `compose_id`, so asking twice cannot
 * deliver twice: a lost answer is simply asked again. If the second answer is lost too, or the
 * draft is gone after a lost answer (a send removes it), only the Sent folder can tell.
 */
export async function runSend(ref: string, confirmResend: boolean, signal: AbortSignal): Promise<SendEnd> {
  let lost = false
  for (;;) {
    try {
      // The confirmation was for one resend, and the lost answer may have been that one.
      const answer = await sendDraft(ref, { confirmResend: confirmResend && !lost })
      if (!('state' in answer)) {
        return await followSend(answer.send_id, signal)
      }
      // After a lost answer this can be our own delivery; on a first try it cannot be.
      if (!lost && answer.state === 'sent') {
        return { kind: 'already' }
      }
      return ending(answer) ?? (await followSend(answer.send_id, signal, answer))
    } catch (error) {
      const coded = error instanceof ApiRequestError && error.code !== undefined ? error : null
      if (coded?.code === 'delivery_unknown') {
        return { kind: 'unknown' }
      }
      if (draftGone(error)) {
        return lost ? { kind: 'unknown', gone: true } : { kind: 'refused', error, gone: true }
      }
      if (coded !== null && (lost || coded.status < 500)) {
        return { kind: 'refused', error, gone: false }
      }
      if (lost) {
        return { kind: 'unknown' }
      }
      lost = true
      await wait(RETRY_MS, signal)
    }
  }
}

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiRequestError } from '@/api/http.ts'
import { readSend, sendDraft, type SendResult } from '@/api/mail.ts'

import { followSend, runSend } from './send-flow.ts'

vi.mock('@/api/mail.ts', () => ({ readSend: vi.fn(), sendDraft: vi.fn() }))

const send = vi.mocked(sendDraft)
const read = vi.mocked(readSend)

const result = (state: SendResult['state'], fields: Partial<SendResult> = {}): SendResult => ({
  send_id: 's1',
  state,
  code: null,
  filed: true,
  rejected: [],
  notes: [],
  note_codes: [],
  settled: true,
  ...fields,
})

const refusal = (status: number, code: string): ApiRequestError =>
  new ApiRequestError(status, { detail: { code, message: code } }, code)

const signal = new AbortController().signal

describe('a send', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
    send.mockReset()
    read.mockReset()
  })

  it('polls until it is not sending', async () => {
    send.mockResolvedValue({ send_id: 's1' })
    read.mockResolvedValueOnce(result('sending')).mockResolvedValueOnce(result('sent'))
    const end = runSend('r', false, signal)
    await vi.runAllTimersAsync()
    expect(await end).toMatchObject({ kind: 'sent' })
    expect(read).toHaveBeenCalledTimes(2)
  })

  it.each([
    ['settled', result('sent')],
    ['not settled', result('sent', { filed: null, settled: false })],
  ])('takes a first answer of sent, %s, as an earlier delivery, not this one', async (_name, replay) => {
    send.mockResolvedValue(replay)
    expect(await runSend('r', false, signal)).toEqual({ kind: 'already' })
    expect(read).not.toHaveBeenCalled()
  })

  it('asks again after a lost answer: the key makes it safe', async () => {
    send.mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValueOnce(result('sent'))
    const end = runSend('r', false, signal)
    await vi.runAllTimersAsync()
    expect(await end).toMatchObject({ kind: 'sent' })
    expect(send).toHaveBeenCalledTimes(2)
  })

  it('confirms a resend once: the retry after a lost answer does not', async () => {
    send.mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValueOnce({ send_id: 's1' })
    read.mockResolvedValue(result('sent'))
    const end = runSend('r', true, signal)
    await vi.runAllTimersAsync()
    await end
    expect(send.mock.calls.map(([, options]) => options?.confirmResend)).toEqual([true, false])
  })

  it('cannot tell after two lost answers', async () => {
    send.mockRejectedValue(new TypeError('Failed to fetch'))
    const end = runSend('r', false, signal)
    await vi.runAllTimersAsync()
    expect(await end).toEqual({ kind: 'unknown' })
  })

  it('reads a draft gone after a lost answer as unknown: the send may have removed it', async () => {
    send.mockRejectedValueOnce(new TypeError('Failed to fetch')).mockRejectedValueOnce(refusal(404, 'not_found'))
    const end = runSend('r', false, signal)
    await vi.runAllTimersAsync()
    expect(await end).toEqual({ kind: 'unknown', gone: true })
  })

  it.each([
    [404, 'not_found'],
    [409, 'stale_reference'],
  ])('takes %s %s as a ref that no longer holds, so that the next Send can differ', async (status, code) => {
    const refused = refusal(status, code)
    send.mockRejectedValue(refused)
    expect(await runSend('r', false, signal)).toEqual({ kind: 'refused', error: refused, gone: true })
  })

  it('passes a refusal on at once', async () => {
    const refused = refusal(429, 'rate_limited')
    send.mockRejectedValue(refused)
    expect(await runSend('r', false, signal)).toEqual({ kind: 'refused', error: refused, gone: false })
    expect(send).toHaveBeenCalledTimes(1)
  })

  it('takes delivery_unknown as the unknown state', async () => {
    send.mockRejectedValue(refusal(409, 'delivery_unknown'))
    expect(await runSend('r', false, signal)).toEqual({ kind: 'unknown' })
  })

  it('reports failed with its code', async () => {
    read.mockResolvedValue(result('failed', { code: 'recipient_refused' }))
    const end = followSend('s1', signal)
    await vi.runAllTimersAsync()
    expect(await end).toEqual({ kind: 'failed', code: 'recipient_refused' })
  })

  it('keeps following a delivered send until it is settled, past the moment filing is known', async () => {
    send.mockResolvedValue({ send_id: 's1' })
    read
      .mockResolvedValueOnce(result('sent', { filed: null, settled: false }))
      .mockResolvedValueOnce(result('sent', { filed: false, settled: false }))
      .mockResolvedValueOnce(result('sent', { filed: false, note_codes: ['draft_kept'] }))
    const end = runSend('r', false, signal)
    await vi.runAllTimersAsync()
    expect(await end).toMatchObject({ kind: 'sent', result: { filed: false, note_codes: ['draft_kept'] } })
    expect(read).toHaveBeenCalledTimes(3)
  })

  it('follows a replayed result that is not settled yet, after a lost answer', async () => {
    send
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(result('sent', { filed: null, settled: false }))
    read.mockResolvedValue(result('sent', { filed: true }))
    const end = runSend('r', false, signal)
    await vi.runAllTimersAsync()
    expect(await end).toMatchObject({ kind: 'sent', result: { filed: true } })
  })

  it('stays sent when it never settles, soon: a restart can leave it so', async () => {
    send.mockResolvedValue({ send_id: 's1' })
    read.mockResolvedValue(result('sent', { filed: true, settled: false }))
    const start = Date.now()
    const end = runSend('r', false, signal)
    await vi.runAllTimersAsync()
    expect(await end).toMatchObject({ kind: 'sent', result: { filed: true } })
    expect(Date.now() - start).toBeGreaterThan(60_000)
    expect(Date.now() - start).toBeLessThan(90_000)
  })

  it('waits out both calls after a delivery, each of which can take 30 s: the notes come last', async () => {
    send.mockResolvedValue({ send_id: 's1' })
    const start = Date.now()
    read.mockImplementation(() =>
      Promise.resolve(
        Date.now() - start < 65_000
          ? result('sent', { filed: false, settled: false })
          : result('sent', { filed: false, note_codes: ['draft_kept'] }),
      ),
    )
    const end = runSend('r', false, signal)
    await vi.runAllTimersAsync()
    expect(await end).toMatchObject({ kind: 'sent', result: { note_codes: ['draft_kept'] } })
  })

  it('never turns a delivered send into unknown when polling breaks', async () => {
    send.mockResolvedValue({ send_id: 's1' })
    read
      .mockResolvedValueOnce(result('sent', { filed: null, settled: false }))
      .mockRejectedValue(new TypeError('Failed to fetch'))
    const end = runSend('r', false, signal)
    await vi.runAllTimersAsync()
    expect(await end).toMatchObject({ kind: 'sent', result: { filed: null } })
  })

  it('rides out a poll that fails, and gives up as unknown', async () => {
    read.mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValue(result('sending'))
    const end = followSend('s1', signal)
    await vi.runAllTimersAsync()
    expect(await end).toEqual({ kind: 'unknown' })
    expect(read.mock.calls.length).toBeGreaterThan(5)
  })
})

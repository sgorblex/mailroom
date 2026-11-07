import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiRequestError } from '@/api/http.ts'
import type { FoundDraft, SavedDraft } from '@/api/mail.ts'

import { debounceFor, SaveQueue } from './save-queue.ts'

const answer = (ref: string | null, fields: Partial<SavedDraft> = {}): SavedDraft => ({
  ref,
  parts: [],
  replaced: true,
  autosave: true,
  ...fields,
})

const refusal = (status: number, code: string): ApiRequestError =>
  new ApiRequestError(status, { detail: { code, message: code } }, code)

interface Harness {
  queue: SaveQueue<string>
  text: { value: string }
  saves: { snapshot: string; ref: string | null; keepalive: boolean }[]
  save: ReturnType<typeof vi.fn<(snapshot: string, ref: string | null, keepalive: boolean) => Promise<SavedDraft>>>
  find: ReturnType<typeof vi.fn<() => Promise<FoundDraft | null>>>
  landed: ReturnType<typeof vi.fn<(snapshot: string | null, ref: string) => void>>
  lost: ReturnType<typeof vi.fn<(ref: string) => void>>
}

function harness(ref: string | null = null, bytes = 0): Harness {
  const text = { value: 'a' }
  const saves: Harness['saves'] = []
  let version = 0
  const save = vi.fn((snapshot: string, from: string | null, keepalive: boolean) => {
    saves.push({ snapshot, ref: from, keepalive })
    version += 1
    return Promise.resolve(answer(`v${String(version)}`))
  })
  const find = vi.fn<() => Promise<FoundDraft | null>>(() => Promise.resolve(null))
  const landed = vi.fn<(snapshot: string | null, ref: string) => void>()
  const lost = vi.fn<(ref: string) => void>()
  const queue = new SaveQueue<string>(
    {
      snapshot: (textOnly) => (textOnly ? `text:${text.value}` : text.value),
      bytes: () => bytes,
      save,
      find,
      landed,
      lost,
      changed: () => undefined,
    },
    ref,
  )
  return { queue, text, saves, save, find, landed, lost }
}

/** Settles what the queue started, timers included. */
async function drain(ms = 0): Promise<void> {
  await vi.advanceTimersByTimeAsync(ms)
}

describe('SaveQueue', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('saves once after a pause, however many edits came', async () => {
    const { queue, saves } = harness()
    queue.touch()
    await drain(1_000)
    queue.touch()
    await drain(1_000)
    queue.touch()
    await drain(debounceFor(0))
    expect(saves).toHaveLength(1)
    expect(queue.state).toBe('saved')
    expect(queue.dirty).toBe(false)
  })

  it('saves someone who never pauses', async () => {
    const { queue, saves } = harness()
    for (let tick = 0; tick < 12; tick += 1) {
      queue.touch()
      await drain(1_000)
    }
    expect(saves.length).toBeGreaterThan(0)
  })

  it('waits longer the larger the draft is', () => {
    expect(debounceFor(10 * 2 ** 20)).toBeGreaterThan(debounceFor(2 ** 20))
    expect(debounceFor(2 ** 40)).toBe(60_000)
  })

  it('runs one save at a time, the next from the ref the first returned', async () => {
    const { queue, saves, save, text } = harness()
    let finish: (value: SavedDraft) => void = () => undefined
    save.mockImplementationOnce((snapshot, ref, keepalive) => {
      saves.push({ snapshot, ref, keepalive })
      return new Promise((resolve) => {
        finish = resolve
      })
    })
    queue.touch()
    await drain(debounceFor(0))
    text.value = 'b'
    queue.touch()
    await drain(10 * debounceFor(0))
    expect(saves).toHaveLength(1)
    finish(answer('v7'))
    await drain(debounceFor(0))
    expect(saves).toEqual([
      { snapshot: 'a', ref: null, keepalive: false },
      { snapshot: 'b', ref: 'v7', keepalive: false },
    ])
  })

  it('finds a save whose answer was lost, and does not write it twice', async () => {
    const { queue, saves, save, find, landed } = harness('v0')
    save.mockImplementationOnce((snapshot, ref, keepalive) => {
      saves.push({ snapshot, ref, keepalive })
      return Promise.reject(new TypeError('Failed to fetch'))
    })
    find.mockResolvedValueOnce({ ref: 'v9', parts: [] })
    queue.touch()
    await drain(debounceFor(0))
    expect(queue.ref).toBe('v9')
    expect(queue.state).toBe('saved')
    expect(queue.dirty).toBe(false)
    expect(saves).toHaveLength(1)
    expect(landed).toHaveBeenCalledWith('a', 'v9', [])
  })

  it('saves over a version found after a lost answer before a send: it may be another writer\'s', async () => {
    const { queue, saves, save, find } = harness('R1')
    save.mockImplementationOnce((snapshot, ref, keepalive) => {
      saves.push({ snapshot, ref, keepalive })
      return Promise.reject(refusal(502, 'server_error'))
    })
    find.mockResolvedValueOnce({ ref: 'R2', parts: [] })
    queue.touch()
    const sendable = queue.flush(true)
    await drain()
    expect(await sendable).toBe(true)
    expect(saves.map(({ ref }) => ref)).toEqual(['R1', 'R2'])
    expect(queue.ref).toBe('v1')
  })

  it('saves over such a version when the send comes after it was found', async () => {
    const { queue, saves, save, find } = harness('R1')
    save.mockImplementationOnce(() => Promise.reject(new TypeError('Failed to fetch')))
    find.mockResolvedValueOnce({ ref: 'R2', parts: [] })
    queue.touch()
    await drain(debounceFor(0))
    expect(queue.dirty).toBe(false)
    expect(saves).toEqual([])

    const sendable = queue.flush(true)
    await drain()
    expect(await sendable).toBe(true)
    expect(saves.map(({ ref }) => ref)).toEqual(['R2'])
  })

  it('is not ready for a send while each save it finds lost its answer', async () => {
    const { queue, save, find } = harness('R1')
    save.mockRejectedValue(new TypeError('Failed to fetch'))
    find.mockResolvedValueOnce({ ref: 'R2', parts: [] }).mockResolvedValueOnce({ ref: 'R3', parts: [] })
    queue.touch()
    const sendable = queue.flush(true)
    await drain()
    expect(await sendable).toBe(false)
    expect(queue.ref).toBe('R3')
  })

  it('keeps the last good draft when a lost save did not land, and says so', async () => {
    const { queue, save, find } = harness('v0')
    save.mockRejectedValueOnce(new ApiRequestError(502, null, 'bad gateway'))
    find.mockResolvedValueOnce({ ref: 'v0', parts: [] })
    queue.touch()
    await drain(debounceFor(0))
    expect(queue.ref).toBe('v0')
    expect(queue.state).toBe('failed')
    expect(queue.dirty).toBe(true)
    expect(await queue.flush()).toBe(true)
    expect(save).toHaveBeenLastCalledWith('a', 'v0', false)
  })

  it('asks again before the next save when even the search failed', async () => {
    const { queue, save, find } = harness('v0')
    save.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    find.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    find.mockResolvedValueOnce({ ref: 'v5', parts: [] })
    queue.touch()
    await drain(debounceFor(0))
    expect(queue.state).toBe('failed')
    expect(await queue.flush()).toBe(true)
    expect(queue.ref).toBe('v5')
    expect(save).toHaveBeenCalledTimes(1)
  })

  describe('refused for the send that holds the draft', () => {
    const send = { send_id: 's1', state: 'sent', code: null }
    const held = (body: object = { send }): ApiRequestError =>
      new ApiRequestError(409, { detail: { code: 'draft_not_editable', message: 'no', ...body } }, 'no')

    it('saves no more, on any path, and says which send, without asking for the draft', async () => {
      const { queue, save, find } = harness('r1')
      save.mockRejectedValueOnce(held())
      queue.touch()
      await vi.advanceTimersByTimeAsync(3_000)
      expect(queue.holding).toEqual(send)
      expect(queue.ref).toBe('r1')

      queue.touch()
      await vi.advanceTimersByTimeAsync(60_000)
      queue.flushHidden()
      expect(await queue.flush(true)).toBe(false)
      queue.resume()
      await vi.advanceTimersByTimeAsync(60_000)

      expect(save).toHaveBeenCalledTimes(1)
      expect(find).not.toHaveBeenCalled()
    })

    it('goes on with what was typed once released', async () => {
      const { queue, save, text } = harness('r1')
      save.mockRejectedValueOnce(held())
      queue.touch()
      await vi.advanceTimersByTimeAsync(3_000)
      text.value = 'b'

      queue.release()
      await vi.advanceTimersByTimeAsync(3_000)

      expect(queue.holding).toBeNull()
      expect(save).toHaveBeenCalledTimes(2)
      expect(queue.state).toBe('saved')
    })

    it('stays an ordinary failure when the refusal names no send', async () => {
      const { queue, save } = harness('r1')
      save.mockRejectedValueOnce(held({}))
      queue.touch()
      await vi.advanceTimersByTimeAsync(3_000)
      expect(queue.holding).toBeNull()
      expect(queue.state).toBe('failed')
    })
  })

  it('does not search after a plain refusal: nothing was stored', async () => {
    const { queue, save, find } = harness('v0')
    save.mockRejectedValueOnce(refusal(422, 'recipient_refused'))
    queue.touch()
    await drain(debounceFor(0))
    expect(queue.state).toBe('failed')
    expect(queue.ref).toBe('v0')
    expect(find).not.toHaveBeenCalled()
  })

  it('does not retry a failure by itself, but an edit tries again', async () => {
    const { queue, save, text } = harness('v0')
    save.mockRejectedValueOnce(refusal(429, 'busy'))
    queue.touch()
    await drain(10 * debounceFor(0))
    expect(save).toHaveBeenCalledTimes(1)
    text.value = 'b'
    queue.touch()
    await drain(debounceFor(0))
    expect(save).toHaveBeenCalledTimes(2)
    expect(queue.state).toBe('saved')
  })

  it('follows the draft when its ref went away, retrying once', async () => {
    const { queue, save, find, saves } = harness('v0')
    save.mockRejectedValueOnce(refusal(409, 'stale_reference'))
    find.mockResolvedValueOnce({ ref: 'v3', parts: [] })
    queue.touch()
    await drain(debounceFor(0))
    expect(saves).toEqual([{ snapshot: 'a', ref: 'v3', keepalive: false }])
    expect(queue.state).toBe('saved')
  })

  it('starts over when the draft is gone: the text is what matters', async () => {
    const { queue, save, saves, lost } = harness('v0')
    save.mockRejectedValueOnce(refusal(404, 'not_found'))
    queue.touch()
    await drain(debounceFor(0))
    expect(saves).toEqual([{ snapshot: 'a', ref: null, keepalive: false }])
    expect(queue.state).toBe('saved')
    // Told before the new save reads the form, which must not name the dead ref.
    expect(lost).toHaveBeenCalledExactlyOnceWith('v0')
    expect(lost.mock.invocationCallOrder[0]).toBeLessThan(save.mock.invocationCallOrder[1] ?? 0)
  })

  it('marks the form unsaved when it finds another version: what shows is what goes', async () => {
    const { queue, find, saves, lost } = harness('v0')
    find.mockResolvedValueOnce({ ref: 'v3', parts: [] })
    await queue.relocate()
    expect(queue.ref).toBe('v3')
    expect(queue.dirty).toBe(true)
    expect(lost).not.toHaveBeenCalled()
    expect(await queue.flush(true)).toBe(true)
    expect(saves).toEqual([{ snapshot: 'a', ref: 'v3', keepalive: false }])
  })

  it('marks the form unsaved when nothing is left, so closing cannot lose it', async () => {
    const { queue, lost } = harness('v0')
    await queue.relocate()
    expect(queue.ref).toBeNull()
    expect(queue.dirty).toBe(true)
    expect(lost).toHaveBeenCalledExactlyOnceWith('v0')
  })

  it('does not take the old version for a save answered without a ref', async () => {
    const { queue, save, find, landed } = harness('v0')
    save.mockResolvedValueOnce(answer(null, { replaced: false }))
    find.mockResolvedValue({ ref: 'v0', parts: [] })
    queue.touch()
    const first = queue.flush(true)
    await drain(4_000)
    expect(await first).toBe(false)
    expect(queue.ref).toBe('v0')
    expect(landed).not.toHaveBeenCalled()

    find.mockResolvedValue({ ref: 'v9', parts: [] })
    const second = queue.flush(true)
    await drain()
    expect(await second).toBe(true)
    expect(queue.ref).toBe('v9')
    expect(landed).toHaveBeenCalledWith('a', 'v9', [])
    expect(save).toHaveBeenCalledTimes(1)
  })

  it('is not ready for a send while nothing finds such a save', async () => {
    const { queue, save } = harness('v0')
    save.mockResolvedValueOnce(answer(null, { replaced: false }))
    queue.touch()
    const flushed = queue.flush(true)
    await drain(4_000)
    expect(await flushed).toBe(false)
  })

  it('does not write a first version twice while it cannot find the one answered without a ref', async () => {
    const { queue, save } = harness()
    save.mockResolvedValueOnce(answer(null, { replaced: false }))
    queue.touch()
    const first = queue.flush(true)
    await drain(4_000)
    expect(await first).toBe(false)
    const second = queue.flush(true)
    await drain()
    expect(await second).toBe(false)
    expect(save).toHaveBeenCalledTimes(1)
  })

  it('lets a draft be left while it cannot find such a save: it is stored', async () => {
    const { queue, save, find } = harness('v0')
    save.mockResolvedValueOnce(answer(null, { replaced: false }))
    find.mockResolvedValue({ ref: 'v0', parts: [] })
    queue.touch()
    const flushed = queue.flush()
    await drain(4_000)
    expect(await flushed).toBe(true)
    const again = queue.flush()
    await drain()
    expect(await again).toBe(true)
  })

  it('forgets such a save once a later one has a ref', async () => {
    const { queue, save, find, text } = harness('v0')
    save.mockResolvedValueOnce(answer(null, { replaced: false }))
    find.mockResolvedValue({ ref: 'v0', parts: [] })
    queue.touch()
    const first = queue.flush(true)
    await drain(4_000)
    await first
    find.mockClear()
    text.value = 'b'
    queue.touch()
    const second = queue.flush(true)
    await drain()
    expect(await second).toBe(true)
    expect(queue.ref).toBe('v1')
    find.mockClear()
    await drain()
    expect(await queue.flush(true)).toBe(true)
    expect(find).not.toHaveBeenCalled()
  })

  it('looks up a save answered without a ref', async () => {
    const { queue, save, find, landed } = harness()
    save.mockResolvedValueOnce(answer(null))
    find.mockResolvedValueOnce(null)
    find.mockResolvedValueOnce({ ref: 'v4', parts: [] })
    queue.touch()
    await drain(debounceFor(0) + 2_000)
    expect(queue.ref).toBe('v4')
    expect(landed).toHaveBeenCalledWith('a', 'v4', [])
    expect(queue.dirty).toBe(false)
  })

  it('stops autosaving when the server cannot replace drafts', async () => {
    const { queue, save, text } = harness()
    save.mockResolvedValueOnce(answer('v1', { autosave: false }))
    queue.touch()
    await drain(debounceFor(0))
    expect(queue.manual).toBe(true)
    text.value = 'b'
    queue.touch()
    await drain(10 * debounceFor(0))
    expect(save).toHaveBeenCalledTimes(1)
    expect(queue.dirty).toBe(true)
    expect(await queue.flush()).toBe(true)
    expect(save).toHaveBeenCalledTimes(2)
  })

  it('flushes for a send, waiting for the save under way', async () => {
    const { queue, saves, text } = harness()
    queue.touch()
    const first = drain(debounceFor(0))
    text.value = 'b'
    await first
    queue.touch()
    expect(await queue.flush()).toBe(true)
    expect(saves.map((save) => save.ref)).toEqual([null, 'v1'])
  })

  it('saves a draft that was never saved when a send needs its ref', async () => {
    const { queue, saves } = harness()
    expect(await queue.flush()).toBe(true)
    expect(saves).toHaveLength(0)
    expect(await queue.flush(true)).toBe(true)
    expect(saves).toHaveLength(1)
  })

  it('flushes a closing page with text only, and never beside a running save', async () => {
    const { queue, saves, save } = harness()
    queue.touch()
    queue.flushHidden()
    await drain()
    expect(saves).toEqual([{ snapshot: 'text:a', ref: null, keepalive: true }])

    let finish: (value: SavedDraft) => void = () => undefined
    save.mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)))
    queue.touch()
    await drain(debounceFor(0))
    queue.touch()
    queue.flushHidden()
    await drain()
    expect(save).toHaveBeenCalledTimes(2)
    finish(answer('v9'))
    await drain()
  })

  it('has nothing to flush when clean', async () => {
    const { queue, saves } = harness('v0')
    queue.flushHidden()
    await drain()
    expect(saves).toHaveLength(0)
  })

  it('stops for a delete, handing over the latest ref, and can resume', async () => {
    const { queue, save, find } = harness('v0')
    save.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    find.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    queue.touch()
    await drain(debounceFor(0))
    find.mockResolvedValueOnce({ ref: 'v6', parts: [] })
    expect(await queue.stop()).toBe('v6')
    queue.touch()
    await drain(10 * debounceFor(0))
    expect(save).toHaveBeenCalledTimes(1)
    queue.resume()
    await drain(debounceFor(0))
    expect(save).toHaveBeenCalledTimes(2)
  })
})

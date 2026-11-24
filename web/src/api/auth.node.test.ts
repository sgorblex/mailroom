import { afterEach, describe, expect, it, vi } from 'vitest'

import { DEFAULT_APPEARANCE, fetchSession } from './auth.ts'

function stubMe(body: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('fetchSession', () => {
  it('takes every feature from an API that does not name them', async () => {
    stubMe({ user: null })
    const session = await fetchSession()
    expect(session.user).toBeNull()
    expect([...session.features]).toEqual(['mail', 'mcp', 'filters'])
  })

  it('keeps the features it knows and drops the others', async () => {
    stubMe({ user: { username: 'alice', is_admin: false, time_zone: 'Europe/Rome', mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }, features: ['mcp', 'calendar'] })
    const session = await fetchSession()
    expect(session.user).toEqual({ username: 'alice', is_admin: false, time_zone: 'Europe/Rome', mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10, appearance: DEFAULT_APPEARANCE })
    expect([...session.features]).toEqual(['mcp'])
  })

  it('reads an appearance option it does not know as that choice\'s default', async () => {
    const user = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }
    stubMe({ user: { ...user, appearance: { theme: 'sepia', accent: 'rose', font: 'serif' } } })
    expect((await fetchSession()).user?.appearance).toEqual({ ...DEFAULT_APPEARANCE, accent: 'rose' })
    stubMe({ user: { ...user, appearance: 'dark' } })
    expect((await fetchSession()).user?.appearance).toEqual(DEFAULT_APPEARANCE)
  })

  it('takes an empty list as the base only', async () => {
    stubMe({ user: null, features: [] })
    expect((await fetchSession()).features.size).toBe(0)
  })
})

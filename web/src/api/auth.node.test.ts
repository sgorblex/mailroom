import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchSession } from './auth.ts'

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
    stubMe({ user: { username: 'alice', is_admin: false }, features: ['mcp', 'calendar'] })
    const session = await fetchSession()
    expect(session.user).toEqual({ username: 'alice', is_admin: false })
    expect([...session.features]).toEqual(['mcp'])
  })

  it('takes an empty list as the base only', async () => {
    stubMe({ user: null, features: [] })
    expect((await fetchSession()).features.size).toBe(0)
  })
})

import { afterEach, describe, expect, it, vi } from 'vitest'
import { z } from 'zod'

import { ApiRequestError, ApiResponseValidationError, apiGetJson, apiPostJson } from './http.ts'

function stubFetch(status: number, body: string): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve(new Response(body, { status }))),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('apiGetJson', () => {
  const schema = z.object({ status: z.literal('ok') })

  it('returns the body validated against the schema', async () => {
    stubFetch(200, '{"status":"ok"}')
    await expect(apiGetJson('/api/v1/health', { schema })).resolves.toEqual({ status: 'ok' })
  })

  it('rejects a body that does not match the schema', async () => {
    stubFetch(200, '{"status":"down"}')
    await expect(apiGetJson('/api/v1/health', { schema })).rejects.toBeInstanceOf(
      ApiResponseValidationError,
    )
  })

  it('carries the FastAPI detail of an error response', async () => {
    stubFetch(404, '{"detail":"Not Found"}')
    await expect(apiGetJson('/api/v1/nope')).rejects.toEqual(
      new ApiRequestError(404, { detail: 'Not Found' }, 'Not Found'),
    )
  })

  it('does not surface a proxy HTML page as the message', async () => {
    stubFetch(502, '<html><body>Bad Gateway</body></html>')
    await expect(apiGetJson('/api/v1/health')).rejects.toThrow('Request failed (502)')
  })
})

describe('apiPostJson', () => {
  it('sends a JSON body with credentials and resolves a 204 to null', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response(null, { status: 204 })))
    vi.stubGlobal('fetch', fetchMock)
    await expect(apiPostJson('/api/v1/auth/login', { password: 'x' })).resolves.toBeNull()
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(init.method).toBe('POST')
    expect(init.credentials).toBe('include')
    expect(init.body).toBe('{"password":"x"}')
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
  })

  it('carries the FastAPI detail of an error response', async () => {
    stubFetch(401, '{"detail":"Wrong password"}')
    await expect(apiPostJson('/api/v1/auth/login', { password: 'x' })).rejects.toThrow(
      'Wrong password',
    )
  })
})

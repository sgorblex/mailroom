import { afterEach, describe, expect, it, vi } from 'vitest'
import { z } from 'zod'

import { ApiRequestError, ApiResponseValidationError, apiGetJson } from './http.ts'

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

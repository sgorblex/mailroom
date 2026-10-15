import type { z } from 'zod'
import { getApiBaseUrl } from './api-base-url.ts'
import { ApiResponseValidationError, parseResponseBody } from './validate.ts'

export { ApiResponseValidationError }

export interface ApiJsonRequestOptions<T> {
  init?: RequestInit
  schema?: z.ZodType<T>
}

export class ApiRequestError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(status: number, body: unknown, message: string) {
    super(message)
    this.name = 'ApiRequestError'
    this.status = status
    this.body = body
  }
}

const MAX_RENDERABLE_ERROR_BODY_LENGTH = 300

/**
 * A plain-text error body only reaches the UI when it did not come from our API — a proxy or
 * gateway answering ahead of the backend. Those are HTML documents, so anything that looks like
 * one, or is far too long to be a message, degrades to the generic status text.
 */
function isRenderableErrorText(body: string): boolean {
  const trimmed = body.trim()
  return (
    trimmed !== '' && !trimmed.startsWith('<') && trimmed.length <= MAX_RENDERABLE_ERROR_BODY_LENGTH
  )
}

function formatErrorMessage(status: number, body: unknown): string {
  const fallback = `Request failed (${String(status)})`
  if (typeof body === 'string') {
    return isRenderableErrorText(body) ? body : fallback
  }
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const detail = body.detail
    if (typeof detail === 'string') {
      return detail
    }
  }
  return fallback
}

async function parseJsonResponse(response: Response): Promise<unknown> {
  const text = await response.text()
  if (text.trim() === '') {
    return null
  }
  try {
    return JSON.parse(text) as unknown
  } catch {
    return text
  }
}

export function apiErrorMessage(error: unknown, fallbackMessage: string): string {
  if (error instanceof ApiRequestError || error instanceof ApiResponseValidationError) {
    return error.message
  }
  return fallbackMessage
}

export async function apiGetJson<T>(
  path: string,
  options: ApiJsonRequestOptions<T> = {},
): Promise<T> {
  const { init = {}, schema } = options
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: 'GET',
    ...init,
    credentials: 'include',
  })
  const body = await parseJsonResponse(response)
  if (!response.ok) {
    throw new ApiRequestError(response.status, body, formatErrorMessage(response.status, body))
  }
  if (schema !== undefined) {
    return parseResponseBody(body, schema)
  }
  return body as T
}

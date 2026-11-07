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

  /** The API's name for the case (`{"detail": {"code": …}}`), for wording it in the UI. */
  get code(): string | undefined {
    const detail = errorDetail(this.body)
    if (typeof detail === 'object' && detail !== null && 'code' in detail) {
      return typeof detail.code === 'string' ? detail.code : undefined
    }
    return undefined
  }
}

function errorDetail(body: unknown): unknown {
  return typeof body === 'object' && body !== null && 'detail' in body ? body.detail : undefined
}

let sessionEndedListener: (() => void) | null = null

/**
 * Told when a request comes back 401: the session ended under the user (expired, signed out
 * elsewhere, password reset, user removed). The auth provider listens; a failed sign-in is not
 * a session ending, so `/auth/login` does not count.
 */
export function onSessionEnded(listener: (() => void) | null): void {
  sessionEndedListener = listener
}

/** A request outside `apiRequest` (a raw `fetch`) came back 401: the session ended. */
export function reportSessionEnded(): void {
  sessionEndedListener?.()
}

const LOGIN_PATH = '/api/v1/auth/login'

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
  const detail = errorDetail(body)
  if (typeof detail === 'string') {
    return detail
  }
  if (typeof detail === 'object' && detail !== null && 'message' in detail) {
    if (typeof detail.message === 'string') {
      return detail.message
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

async function apiRequest<T>(
  path: string,
  init: RequestInit,
  schema: z.ZodType<T> | undefined,
): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, { ...init, credentials: 'include' })
  const body = await parseJsonResponse(response)
  if (!response.ok) {
    if (response.status === 401 && path !== LOGIN_PATH) {
      reportSessionEnded()
    }
    throw new ApiRequestError(response.status, body, formatErrorMessage(response.status, body))
  }
  if (schema !== undefined) {
    return parseResponseBody(body, schema)
  }
  return body as T
}

export function apiGetJson<T>(path: string, options: ApiJsonRequestOptions<T> = {}): Promise<T> {
  const { init = {}, schema } = options
  return apiRequest(path, { method: 'GET', ...init }, schema)
}

/** POST a JSON body (or none); a 204 resolves to `null` unless a schema says otherwise. */
export function apiPostJson<T = null>(
  path: string,
  body?: unknown,
  options: ApiJsonRequestOptions<T> = {},
): Promise<T> {
  const { init = {}, schema } = options
  const headers = new Headers(init.headers)
  if (body !== undefined) {
    headers.set('Content-Type', 'application/json')
  }
  return apiRequest(
    path,
    {
      method: 'POST',
      ...init,
      headers,
      body: body === undefined ? null : JSON.stringify(body),
    },
    schema,
  )
}

/** POST a multipart form; the browser sets the `Content-Type`, with its boundary. */
export function apiPostForm<T>(path: string, form: FormData, options: ApiJsonRequestOptions<T> = {}): Promise<T> {
  const { init = {}, schema } = options
  return apiRequest(path, { method: 'POST', ...init, body: form }, schema)
}

/** PUT a JSON body, replacing the resource. */
export function apiPutJson<T>(
  path: string,
  body: unknown,
  options: ApiJsonRequestOptions<T> = {},
): Promise<T> {
  const { init = {}, schema } = options
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  return apiRequest(path, { method: 'PUT', ...init, headers, body: JSON.stringify(body) }, schema)
}

/** DELETE; a 204 resolves to `null`. */
export function apiDelete(path: string, options: ApiJsonRequestOptions<null> = {}): Promise<null> {
  const { init = {} } = options
  return apiRequest(path, { method: 'DELETE', ...init }, undefined)
}

/** DELETE, with an answer to read. */
export function apiDeleteJson<T>(path: string, options: ApiJsonRequestOptions<T> = {}): Promise<T> {
  const { init = {}, schema } = options
  return apiRequest(path, { method: 'DELETE', ...init }, schema)
}

/** PATCH a JSON body: only the fields it carries change. */
export function apiPatchJson<T>(
  path: string,
  body: unknown,
  options: ApiJsonRequestOptions<T> = {},
): Promise<T> {
  const { init = {}, schema } = options
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  return apiRequest(path, { method: 'PATCH', ...init, headers, body: JSON.stringify(body) }, schema)
}

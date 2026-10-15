import { z } from 'zod'

export class ApiResponseValidationError extends Error {
  readonly zodError: z.ZodError

  constructor(zodError: z.ZodError) {
    super('Unexpected response from server')
    Object.setPrototypeOf(this, new.target.prototype)
    this.name = 'ApiResponseValidationError'
    this.zodError = zodError
    if ('captureStackTrace' in Error) {
      ;(Error as ErrorConstructor & {
        captureStackTrace(error: Error): void
      }).captureStackTrace(this)
    }
  }
}

export function parseResponseBody<T>(body: unknown, schema: z.ZodType<T>): T {
  const result = schema.safeParse(body)
  if (!result.success) {
    throw new ApiResponseValidationError(result.error)
  }
  return result.data
}

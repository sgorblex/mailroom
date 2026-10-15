import { z } from 'zod'
import { apiGetJson } from './http.ts'

export const healthSchema = z.object({ status: z.literal('ok') })

export type Health = z.infer<typeof healthSchema>

export function fetchHealth(signal?: AbortSignal): Promise<Health> {
  return apiGetJson('/api/v1/health', { init: { signal }, schema: healthSchema })
}

import { z } from 'zod'
import { apiGetJson, apiPutJson } from './http.ts'

/** The most a written message's attachments may total, whatever the user's limit. */
export const ATTACHMENT_MAX_MB = 25

/** The user's mail limits, for the web and MCP alike: sending, and the window a body is read in. */
export const limitsSchema = z.object({
  send_per_hour: z.number(),
  max_recipients: z.number(),
  body_chars: z.number(),
  attachment_max_mb: z.number(),
})
export type Limits = z.infer<typeof limitsSchema>

const path = '/api/v1/me/limits'

export function getLimits(signal?: AbortSignal): Promise<Limits> {
  return apiGetJson(path, { init: { signal }, schema: limitsSchema })
}

export function changeLimits(limits: Limits): Promise<Limits> {
  return apiPutJson(path, limits, { schema: limitsSchema })
}

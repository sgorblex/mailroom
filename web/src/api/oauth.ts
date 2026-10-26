import { z } from 'zod'
import { apiGetJson, apiPostJson } from './http.ts'

export const consentRequestSchema = z.object({
  client_name: z.string().nullable(),
  client_host: z.string().nullable(),
  redirect_host: z.string(),
  scopes: z.array(z.string()),
  expires_at: z.string(),
})
export type ConsentRequest = z.infer<typeof consentRequestSchema>

const decidedSchema = z.object({ redirect_to: z.string() })

function requestPath(requestId: string): string {
  return `/api/v1/oauth/requests/${encodeURIComponent(requestId)}`
}

/** The authorization request an app sent the browser here with; a 404 once it is gone. */
export function fetchConsentRequest(requestId: string, signal?: AbortSignal): Promise<ConsentRequest> {
  return apiGetJson(requestPath(requestId), { init: { signal }, schema: consentRequestSchema })
}

/** Approve or deny it; resolves to where the browser goes next, back to the app. */
export async function decideConsentRequest(
  requestId: string,
  decision: 'approve' | 'deny',
): Promise<string> {
  const decided = await apiPostJson(requestPath(requestId), { decision }, { schema: decidedSchema })
  return decided.redirect_to
}

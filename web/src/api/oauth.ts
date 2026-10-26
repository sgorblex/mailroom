import { z } from 'zod'
import { apiDelete, apiGetJson, apiPostJson } from './http.ts'

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

export const connectedAppSchema = z.object({
  id: z.number(),
  client_name: z.string().nullable(),
  client_host: z.string().nullable(),
  created_at: z.string(),
  last_used_at: z.string().nullable(),
})
export type ConnectedApp = z.infer<typeof connectedAppSchema>

export const connectedAppsSchema = z.object({
  /** How MCP clients get their tokens: `external` means the identity provider manages access. */
  auth: z.enum(['builtin', 'external', 'off']),
  grants: z.array(connectedAppSchema),
})
export type ConnectedApps = z.infer<typeof connectedAppsSchema>

/** The apps the user approved through the built-in authorization server. */
export function listConnectedApps(signal?: AbortSignal): Promise<ConnectedApps> {
  return apiGetJson('/api/v1/oauth/grants', { init: { signal }, schema: connectedAppsSchema })
}

/** Disconnect one: its tokens stop working at once. */
export function revokeConnectedApp(id: number): Promise<null> {
  return apiDelete(`/api/v1/oauth/grants/${String(id)}`)
}

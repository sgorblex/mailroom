import { z } from 'zod'
import { SEND_MODES, accountIdSchema } from './accounts.ts'
import { apiDelete, apiGetJson, apiPostJson, apiPutJson } from './http.ts'

/** An MCP tool group the instance serves, as the user approves it for an app. */
export const toolGroupSchema = z.object({
  name: z.string(),
  description: z.string(),
  /** Groups it can be approved only with. */
  requires: z.array(z.string()),
  /** Offered checked: not for groups that send mail or delete it for good. */
  default: z.boolean(),
})
export type ToolGroup = z.infer<typeof toolGroupSchema>

/** One of the user's accounts an app can be narrowed to. */
export const accountChoiceSchema = z.object({ id: accountIdSchema, name: z.string() })
export type AccountChoice = z.infer<typeof accountChoiceSchema>

export const limitsSchema = z.object({
  /** The accounts it sees; `null` for every one, those added later too. */
  account_ids: z.array(accountIdSchema).nullable().default(null),
  /** The most it may do when it sends, whatever an account's mode; `null` for no ceiling. */
  send_ceiling: z.enum(SEND_MODES).nullable().default(null),
})
/** What an app is limited to, beyond its tool groups. */
export type AppLimits = z.infer<typeof limitsSchema>

export const consentRequestSchema = z.object({
  client_name: z.string().nullable(),
  client_host: z.string().nullable(),
  redirect_host: z.string(),
  scopes: z.array(z.string()),
  groups: z.array(toolGroupSchema),
  /** The user's accounts, for them to choose from. */
  accounts: z.array(accountChoiceSchema).default([]),
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

/**
 * Approve it for the tool groups and within the limits chosen, or deny it; resolves to where the browser goes next,
 * back to the app.
 */
export async function decideConsentRequest(
  requestId: string,
  decision: ({ decision: 'approve'; groups: string[] } & AppLimits) | { decision: 'deny' },
): Promise<string> {
  const decided = await apiPostJson(requestPath(requestId), decision, { schema: decidedSchema })
  return decided.redirect_to
}

export const connectedAppSchema = limitsSchema.extend({
  id: z.number(),
  client_name: z.string().nullable(),
  client_host: z.string().nullable(),
  created_at: z.string(),
  last_used_at: z.string().nullable(),
  /** The served tool groups it is approved for. */
  groups: z.array(z.string()),
})
export type ConnectedApp = z.infer<typeof connectedAppSchema>

export const connectedAppsSchema = z.object({
  /**
   * How MCP clients get their tokens: `oidc` is the built-in server with sign-in through SSO;
   * `external` means the identity provider manages access.
   */
  auth: z.enum(['builtin', 'oidc', 'external', 'off']),
  grants: z.array(connectedAppSchema),
  /** The tool groups served, which an app's are chosen from; none without the built-in server. */
  groups: z.array(toolGroupSchema),
  /** The user's accounts, which an app can be narrowed to. */
  accounts: z.array(accountChoiceSchema).default([]),
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

/** Make `groups` the tool groups the app is approved for: its next call sees them. */
export function setConnectedAppGroups(id: number, groups: string[]): Promise<null> {
  return apiPutJson<null>(`/api/v1/oauth/grants/${String(id)}/groups`, { groups })
}

/** Make `limits` the app's, both of them: its next call sees them. */
export function setConnectedAppLimits(id: number, limits: AppLimits): Promise<null> {
  return apiPutJson<null>(`/api/v1/oauth/grants/${String(id)}/limits`, limits)
}

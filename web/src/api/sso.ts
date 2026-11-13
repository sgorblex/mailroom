import { z } from 'zod'
import { getApiBaseUrl } from './api-base-url.ts'
import { apiDelete, apiGetJson } from './http.ts'

export const ssoConfigSchema = z.object({ enabled: z.boolean(), name: z.string().nullable() })
export type SsoConfig = z.infer<typeof ssoConfigSchema>

/** Whether sign-in through the identity provider is on, and its button's label. */
export function fetchSsoConfig(signal?: AbortSignal): Promise<SsoConfig> {
  return apiGetJson('/api/v1/auth/oidc/config', { init: { signal }, schema: ssoConfigSchema })
}

/**
 * Where the browser goes to sign in (or, signed in, to link a sign-in) through the provider.
 * A navigation, not a fetch: the API redirects to the provider, which comes back to `returnTo`.
 */
export function ssoStartUrl(intent: 'login' | 'link', returnTo: string): string {
  const query = new URLSearchParams({ intent, return_to: returnTo })
  return `${getApiBaseUrl()}/api/v1/auth/oidc/start?${query.toString()}`
}

/**
 * Where the browser goes to sign in through the provider for an app's authorization request,
 * when Mailroom only lets a sign-in through SSO answer one. The API comes back to its consent
 * screen.
 */
export function mcpSignInUrl(requestId: string): string {
  const query = new URLSearchParams({ intent: 'mcp', request: requestId })
  return `${getApiBaseUrl()}/api/v1/auth/oidc/start?${query.toString()}`
}

export const identitySchema = z.object({
  id: z.number(),
  issuer: z.string(),
  subject: z.string(),
  name: z.string().nullable(),
  created_at: z.string(),
  last_used_at: z.string().nullable(),
})
export type Identity = z.infer<typeof identitySchema>

export function listIdentities(signal?: AbortSignal): Promise<Identity[]> {
  return apiGetJson('/api/v1/me/identities', { init: { signal }, schema: z.array(identitySchema) })
}

export function unlinkIdentity(id: number): Promise<null> {
  return apiDelete(`/api/v1/me/identities/${String(id)}`)
}

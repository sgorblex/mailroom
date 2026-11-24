import { z } from 'zod'
import { apiDelete, apiGetJson, apiPostJson } from './http.ts'
import { accountChoiceSchema, limitsSchema, toolGroupSchema, type AppLimits } from './oauth.ts'

export const apiTokenSchema = limitsSchema.extend({
  id: z.number(),
  name: z.string(),
  /** What the token starts with. */
  prefix: z.string(),
  created_at: z.string(),
  last_used_at: z.string().nullable(),
  /** `null` for never. */
  expires_at: z.string().nullable(),
  /** Past `expires_at`, by the server's clock. */
  expired: z.boolean(),
  /** The tool groups it holds that the instance offers. */
  groups: z.array(z.string()),
})
export type ApiToken = z.infer<typeof apiTokenSchema>

const apiTokensSchema = z.object({
  tokens: z.array(apiTokenSchema),
  /** The tool groups a token can hold here. */
  groups: z.array(toolGroupSchema),
  /** The user's accounts, which a token can be narrowed to. */
  accounts: z.array(accountChoiceSchema),
})
export type ApiTokens = z.infer<typeof apiTokensSchema>

/** How long a token lasts, in days. */
export const EXPIRIES = [30, 90, 365] as const
export type Expiry = (typeof EXPIRIES)[number]

export interface NewApiToken extends AppLimits {
  name: string
  groups: string[]
  /** `null` for never. */
  expires_in_days: Expiry | null
}

/** `token` is the token itself, in this answer only. */
const createdSchema = apiTokenSchema.extend({ token: z.string() })
export type CreatedApiToken = z.infer<typeof createdSchema>

const PATH = '/api/v1/me/api-tokens'

/** The user's personal API tokens, newest first, and what a new one can be given. */
export function listApiTokens(signal?: AbortSignal): Promise<ApiTokens> {
  return apiGetJson(PATH, { init: { signal }, schema: apiTokensSchema })
}

export function createApiToken(token: NewApiToken): Promise<CreatedApiToken> {
  return apiPostJson(PATH, token, { schema: createdSchema })
}

/** Revoke one: it stops working at once. */
export function revokeApiToken(id: number): Promise<null> {
  return apiDelete(`${PATH}/${String(id)}`)
}

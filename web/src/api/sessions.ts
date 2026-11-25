import { z } from 'zod'
import { apiDelete, apiGetJson, apiPostJson } from './http.ts'

export const signedInSessionSchema = z.object({
  id: z.number(),
  /** The one this browser is signed in with. */
  current: z.boolean(),
  /** Browser and system (`Firefox · Linux`), or what is known of them. */
  device: z.string().nullable(),
  /** As much as the instance records: its last part may be an `x`, and it may be none. */
  ip: z.string().nullable(),
  created_at: z.string(),
  /** To within five minutes. */
  last_seen_at: z.string(),
  expires_at: z.string(),
})
export type SignedInSession = z.infer<typeof signedInSessionSchema>

/** The user's signed-in sessions, this browser's first. */
export function listSessions(signal?: AbortSignal): Promise<SignedInSession[]> {
  return apiGetJson('/api/v1/auth/sessions', { init: { signal }, schema: z.array(signedInSessionSchema) })
}

/** Sign one out, not this browser's. */
export function endSession(id: number): Promise<null> {
  return apiDelete(`/api/v1/auth/sessions/${String(id)}`)
}

/** Sign out every session but this browser's. */
export function endOtherSessions(): Promise<null> {
  return apiPostJson('/api/v1/auth/sessions/end-others')
}

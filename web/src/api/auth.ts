import { z } from 'zod'
import { apiGetJson, apiPostJson } from './http.ts'

export const userSchema = z.object({ username: z.string(), is_admin: z.boolean() })
export type User = z.infer<typeof userSchema>

/** The optional parts of an instance, on top of the base that is always there. */
export const FEATURES = ['mail', 'mcp', 'filters'] as const
export type Feature = (typeof FEATURES)[number]
export const ALL_FEATURES: ReadonlySet<Feature> = new Set(FEATURES)

const isFeature = (name: string): name is Feature => (FEATURES as readonly string[]).includes(name)

/** `features` is absent from an API older than editions, which runs them all. */
export const meSchema = z.object({
  user: userSchema.nullable(),
  features: z.array(z.string()).optional(),
})

export interface Session {
  /** Who this browser is signed in as, or `null`. */
  user: User | null
  /** The instance's features; names this app does not know are dropped. */
  features: ReadonlySet<Feature>
}

export async function fetchSession(signal?: AbortSignal): Promise<Session> {
  const me = await apiGetJson('/api/v1/auth/me', { init: { signal }, schema: meSchema })
  return {
    user: me.user,
    features: me.features === undefined ? ALL_FEATURES : new Set(me.features.filter(isFeature)),
  }
}

export function login(username: string, password: string): Promise<User> {
  return apiPostJson('/api/v1/auth/login', { username, password }, { schema: userSchema })
}

export function logout(): Promise<null> {
  return apiPostJson('/api/v1/auth/logout')
}

/** Keeps this session and ends the others. */
export function changePassword(currentPassword: string, newPassword: string): Promise<null> {
  return apiPostJson('/api/v1/auth/password', {
    current_password: currentPassword,
    new_password: newPassword,
  })
}

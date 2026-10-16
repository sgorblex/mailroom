import { z } from 'zod'
import { apiGetJson, apiPostJson } from './http.ts'

export const userSchema = z.object({ username: z.string(), is_admin: z.boolean() })
export type User = z.infer<typeof userSchema>

export const meSchema = z.object({ user: userSchema.nullable() })

/** Who this browser is signed in as, or `null`. */
export async function fetchCurrentUser(signal?: AbortSignal): Promise<User | null> {
  const me = await apiGetJson('/api/v1/auth/me', { init: { signal }, schema: meSchema })
  return me.user
}

export function login(username: string, password: string): Promise<User> {
  return apiPostJson('/api/v1/auth/login', { username, password }, { schema: userSchema })
}

export function logout(): Promise<null> {
  return apiPostJson('/api/v1/auth/logout')
}

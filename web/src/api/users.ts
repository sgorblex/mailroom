import { z } from 'zod'
import { apiDelete, apiGetJson, apiPostJson } from './http.ts'

export const listedUserSchema = z.object({
  id: z.number(),
  username: z.string(),
  is_admin: z.boolean(),
  created_at: z.string(),
})
export type ListedUser = z.infer<typeof listedUserSchema>

/** Admins only, like everything in this module. */
export function listUsers(signal?: AbortSignal): Promise<ListedUser[]> {
  return apiGetJson('/api/v1/users', { init: { signal }, schema: z.array(listedUserSchema) })
}

export function createUser(
  username: string,
  password: string,
  isAdmin: boolean,
): Promise<ListedUser> {
  return apiPostJson(
    '/api/v1/users',
    { username, password, is_admin: isAdmin },
    { schema: listedUserSchema },
  )
}

export function removeUser(id: number): Promise<null> {
  return apiDelete(`/api/v1/users/${String(id)}`)
}

/** Signs them out everywhere. */
export function resetPassword(id: number, password: string): Promise<null> {
  return apiPostJson(`/api/v1/users/${String(id)}/password`, { password })
}

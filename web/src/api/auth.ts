import { z } from 'zod'
import { apiGetJson, apiPostJson, apiPutJson } from './http.ts'

/**
 * `time_zone`: an IANA name, `null` until the user (or their first sign-in) sets one.
 * `mark_read_subfolders`: what "Mark all as read" on a folder with subfolders does first: `true`, them too.
 * `index_max_age_days`: the user's own age limit of the mail index, in days; `null` for none (the instance's cap still holds).
 * `conversations`: whether the message lists are grouped by conversation.
 */
export const userSchema = z.object({
  username: z.string(),
  is_admin: z.boolean(),
  time_zone: z.string().nullable(),
  mark_read_subfolders: z.boolean(),
  index_max_age_days: z.number().nullable(),
  conversations: z.boolean(),
})
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

/** The zone the user's dates are written in, here and over MCP; `null` unsets it. */
export function changeTimeZone(timeZone: string | null): Promise<User> {
  return apiPutJson('/api/v1/auth/me/time-zone', { time_zone: timeZone }, { schema: userSchema })
}

/** Whether "Mark all as read" on a folder with subfolders marks them too, unless the user picks the other item. */
export function changeMarkReadSubfolders(subfolders: boolean): Promise<User> {
  return apiPutJson('/api/v1/auth/me/mark-read-subfolders', { mark_read_subfolders: subfolders }, { schema: userSchema })
}

/** How far back the mail index keeps the user's mail, in days; `null`: no limit of their own. */
export function changeIndexMaxAge(days: number | null): Promise<User> {
  return apiPutJson('/api/v1/auth/me/index-max-age', { index_max_age_days: days }, { schema: userSchema })
}

/** Whether the message lists are grouped by conversation. */
export function changeConversations(conversations: boolean): Promise<User> {
  return apiPutJson('/api/v1/auth/me/conversations', { conversations }, { schema: userSchema })
}

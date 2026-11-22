import { z } from 'zod'
import { apiGetJson, apiPostJson, apiPutJson } from './http.ts'

export const THEMES = ['system', 'light', 'dark'] as const
export const ACCENTS = ['default', 'blue', 'violet', 'green', 'terracotta', 'rose'] as const
export const DENSITIES = ['comfortable', 'compact'] as const
export const TEXT_SIZES = ['small', 'default', 'large'] as const
export const READING_PANES = ['right', 'below', 'off'] as const
export const HOMES = ['dashboard', 'mail', 'filters', 'accounts', 'activity'] as const
export const SWIPE_ACTIONS = ['none', 'archive', 'trash', 'read', 'star', 'to_sort'] as const

/**
 * How the app looks to the user, kept on the server so every device shows the same; choices of one
 * device alone (pane widths) stay in its browser (`lib/device-prefs.ts`). An option this app does
 * not know (a newer or older server) reads as that choice's default, so sign-in never fails on it.
 * `home`: the page the app opens on; `swipe_left`, `swipe_right`: what swiping a message row does.
 */
export const appearanceSchema = z.object({
  theme: z.enum(THEMES).catch('system'),
  accent: z.enum(ACCENTS).catch('default'),
  density: z.enum(DENSITIES).catch('comfortable'),
  text_size: z.enum(TEXT_SIZES).catch('default'),
  reading_pane: z.enum(READING_PANES).catch('right'),
  home: z.enum(HOMES).catch('dashboard'),
  swipe_left: z.enum(SWIPE_ACTIONS).catch('trash'),
  swipe_right: z.enum(SWIPE_ACTIONS).catch('archive'),
})
export type Appearance = z.infer<typeof appearanceSchema>

export const DEFAULT_APPEARANCE: Readonly<Appearance> = Object.freeze(appearanceSchema.parse({}))

/**
 * `time_zone`: an IANA name, `null` until the user (or their first sign-in) sets one.
 * `mark_read_subfolders`: what "Mark all as read" on a folder with subfolders does first: `true`, them too.
 * `index_max_age_days`: the user's own age limit of the mail index, in days; `null` for none (the instance's cap still holds).
 * `conversations`: whether the message lists are grouped by conversation.
 * `undo_send_seconds`: how long Send holds a message for an Undo; 0 sends at once.
 * `appearance`: how the app looks, the same on every device; absent from an older API, so its defaults.
 */
export const userSchema = z.object({
  username: z.string(),
  is_admin: z.boolean(),
  time_zone: z.string().nullable(),
  mark_read_subfolders: z.boolean(),
  index_max_age_days: z.number().nullable(),
  conversations: z.boolean(),
  undo_send_seconds: z.number(),
  appearance: appearanceSchema.catch(() => ({ ...DEFAULT_APPEARANCE })).default(() => ({ ...DEFAULT_APPEARANCE })),
})
export type User = z.infer<typeof userSchema>
/** A user as the API may send one: `appearance` may be missing or partly unknown. */
export type UserJson = z.input<typeof userSchema>

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

/** The longest Send can wait for an Undo, in seconds (the server's limit too). */
export const UNDO_SEND_SECONDS = 30

/** How long Send holds a message for an Undo, in seconds; 0 sends at once. */
export function changeUndoSend(seconds: number): Promise<User> {
  return apiPutJson('/api/v1/auth/me/undo-send', { undo_send_seconds: seconds }, { schema: userSchema })
}

/** Some of the appearance choices, kept over the others; the answer carries them all. */
export function changeAppearance(change: Partial<Appearance>): Promise<User> {
  return apiPutJson('/api/v1/auth/me/appearance', change, { schema: userSchema })
}

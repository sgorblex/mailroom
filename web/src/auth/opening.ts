import { z } from 'zod'

import { HOMES } from '@/api/auth.ts'
import { readDevicePref } from '@/lib/device-prefs.ts'

const openingSchema = z.object({ home: z.enum(HOMES), mail: z.boolean() })

/**
 * What this browser last learnt from `/me` about where the app opens: the user's `home` and whether
 * the instance runs mail. The next load reads it before `/me` answers, to start fetching the mail
 * screen's code alongside it (`preloadMailOpening`); `/me` decides as before. `OpeningSync` writes it.
 */
export type Opening = z.infer<typeof openingSchema>

export const OPENING_PREF = 'opening'

const isOpening = (value: unknown): value is Opening => openingSchema.safeParse(value).success

/** This browser's copy of the opening, or `null` before its first `/me` as a signed-in user. */
export function rememberedOpening(): Opening | null {
  return readDevicePref<Opening | null>(OPENING_PREF, null, isOpening)
}

/**
 * Whether a load at `path` opens on the mail screen, as far as this browser knows: `/mail`, or `/`
 * as the tab's first page (`firstPage`) for a user whose home is mail, as `HomeRoute` sends them.
 * Never when the instance last said it runs no mail; at `/mail` before anything is remembered, yes.
 */
export function mailOpening(path: string, firstPage: boolean, remembered: Opening | null): boolean {
  if (remembered?.mail === false) {
    return false
  }
  if (path === '/mail') {
    return true
  }
  return path === '/' && firstPage && remembered?.home === 'mail'
}

/**
 * Starts fetching the mail screen's code when this load opens on it, so it arrives with `/me`
 * instead of after it. The route imports the same module, so it is fetched once; a failure here is
 * left to the route, which loads it again and reports it.
 */
export function preloadMailOpening(path: string, firstPage: boolean): void {
  if (mailOpening(path, firstPage, rememberedOpening())) {
    import('@/mail/mail-screen.tsx').catch(() => undefined)
  }
}

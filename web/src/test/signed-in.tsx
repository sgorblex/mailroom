import { render } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { vi } from 'vitest'

import { ALL_FEATURES, changeConversations, changeIndexMaxAge, changeMarkReadSubfolders, changeTimeZone, type Feature, type User } from '@/api/auth.ts'
import { AuthContext } from '@/auth/auth-context.ts'
import { setRouteFeatures } from '@/auth/feature-route.tsx'
import { createQueryClient } from '@/lib/query-client.ts'

/**
 * Render `ui` as `user` would see it on an instance with `features`, without the sign-in round trip.
 * A time zone, mark-read default, index age limit or conversations change goes to the API but leaves `user` as it is. A cache of
 * its own, never retrying.
 */
export function renderSignedIn(
  ui: ReactElement,
  user: User,
  features: Iterable<Feature> = ALL_FEATURES,
): void {
  const reject = (): Promise<void> => Promise.reject(new Error('not in this test'))
  const enabled = new Set(features)
  setRouteFeatures(enabled)
  render(
    <AuthContext
      value={{
        status: 'signed-in',
        user,
        features: enabled,
        signIn: reject,
        signOut: reject,
        changeTimeZone: async (timeZone) => {
          await changeTimeZone(timeZone)
        },
        changeMarkReadSubfolders: async (subfolders) => {
          await changeMarkReadSubfolders(subfolders)
        },
        changeIndexMaxAge: async (days) => {
          await changeIndexMaxAge(days)
        },
        changeConversations: async (conversations) => {
          await changeConversations(conversations)
        },
        recheck: vi.fn(),
      }}
    >
      <QueryClientProvider client={createQueryClient()}>{ui}</QueryClientProvider>
    </AuthContext>,
  )
}

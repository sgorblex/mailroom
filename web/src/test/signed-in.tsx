import { render } from '@testing-library/react'
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { vi } from 'vitest'

import { ALL_FEATURES, changeAppearance, changeConversations, changeInboxSubfolders, changeUndoSend, changeIndexMaxAge, changeMarkReadSubfolders, changeTimeZone, userSchema, type Feature, type UserJson } from '@/api/auth.ts'
import { AuthContext } from '@/auth/auth-context.ts'
import { setRouteFeatures } from '@/auth/feature-route.tsx'
import { createQueryClient } from '@/lib/query-client.ts'

/**
 * Render `ui` as `user` would see it on an instance with `features`, without the sign-in round trip.
 * A time zone, mark-read default, index age limit, conversations, Undo delay or appearance change goes to the API but leaves `user` as it is. A cache of
 * its own, never retrying, unless given one.
 */
export function renderSignedIn(
  ui: ReactElement,
  user: UserJson,
  features: Iterable<Feature> = ALL_FEATURES,
  queryClient: QueryClient = createQueryClient(),
): void {
  const reject = (): Promise<void> => Promise.reject(new Error('not in this test'))
  const enabled = new Set(features)
  setRouteFeatures(enabled)
  render(
    <AuthContext
      value={{
        status: 'signed-in',
        user: userSchema.parse(user),
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
        changeInboxSubfolders: async (included) => {
          await changeInboxSubfolders(included)
        },
        changeUndoSend: async (seconds) => {
          await changeUndoSend(seconds)
        },
        changeAppearance: async (change) => {
          await changeAppearance(change)
        },
        recheck: vi.fn(),
      }}
    >
      <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>
    </AuthContext>,
  )
}

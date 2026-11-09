import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { vi } from 'vitest'

import { ALL_FEATURES, type Feature, type User } from '@/api/auth.ts'
import { AuthContext } from '@/auth/auth-context.ts'
import { setRouteFeatures } from '@/auth/feature-route.tsx'

/** Render `ui` as `user` would see it on an instance with `features`, without the sign-in round trip. */
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
        recheck: vi.fn(),
      }}
    >
      {ui}
    </AuthContext>,
  )
}

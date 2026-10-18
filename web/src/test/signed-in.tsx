import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { vi } from 'vitest'

import type { User } from '@/api/auth.ts'
import { AuthContext } from '@/auth/auth-context.ts'

/** Render `ui` as `user` would see it, without the sign-in round trip. */
export function renderSignedIn(ui: ReactElement, user: User): void {
  const reject = (): Promise<void> => Promise.reject(new Error('not in this test'))
  render(
    <AuthContext
      value={{ status: 'signed-in', user, signIn: reject, signOut: reject, recheck: vi.fn() }}
    >
      {ui}
    </AuthContext>,
  )
}

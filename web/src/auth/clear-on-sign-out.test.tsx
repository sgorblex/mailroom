import { render } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { createQueryClient } from '@/lib/query-client.ts'

import { AuthContext, type AuthContextValue, type AuthState } from './auth-context.ts'
import { ClearOnSignOut } from './clear-on-sign-out.ts'

const ALICE = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true }

function signedAs(state: AuthState): AuthContextValue {
  const reject = (): Promise<void> => Promise.reject(new Error('not in this test'))
  return {
    ...state,
    features: new Set(),
    signIn: reject,
    signOut: reject,
    changeTimeZone: reject,
    changeMarkReadSubfolders: reject,
    recheck: vi.fn(),
  }
}

describe('ClearOnSignOut', () => {
  it("keeps the cache while signed in, and drops the user's reads at sign-out", () => {
    const queryClient = createQueryClient()
    const app = (state: AuthState): ReactElement => (
      <AuthContext value={signedAs(state)}>
        <QueryClientProvider client={queryClient}>
          <ClearOnSignOut />
        </QueryClientProvider>
      </AuthContext>
    )
    const { rerender } = render(app({ status: 'signed-in', user: ALICE }))
    queryClient.setQueryData(['messages', 'INBOX'], { items: ['a message'] })

    rerender(app({ status: 'signed-in', user: ALICE }))
    expect(queryClient.getQueryCache().getAll()).toHaveLength(1)

    rerender(app({ status: 'signed-out' }))
    expect(queryClient.getQueryCache().getAll()).toEqual([])
  })
})

import { StrictMode } from 'react'
import { render } from '@testing-library/react'
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
  type RouterHistory,
} from '@tanstack/react-router'
import { vi } from 'vitest'

import type { User } from '@/api/auth.ts'
import { AuthContext } from '@/auth/auth-context.ts'
import { MailScreen } from '@/mail/mail-screen.tsx'
import { validateMailSearch } from '@/mail/route-search.ts'

/**
 * The mail screen at `path`, in a router of its own, with Accounts as a placeholder; its history to
 * inspect. `strict` as `main.tsx` renders it; with `user`, signed in as them (else no `AuthProvider`).
 */
export function renderMailApp(path: string, { strict = false, user }: { strict?: boolean; user?: User } = {}): RouterHistory {
  const rootRoute = createRootRoute({ component: Outlet })
  const accountsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/accounts',
    component: () => <p>Accounts page</p>,
  })
  const mailRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/mail',
    validateSearch: validateMailSearch,
    component: MailScreen,
  })
  const history = createMemoryHistory({ initialEntries: [path] })
  const router = createRouter({ routeTree: rootRoute.addChildren([accountsRoute, mailRoute]), history })
  // The router restores scroll positions, which jsdom does not implement.
  vi.stubGlobal('scrollTo', vi.fn())
  const reject = (): Promise<void> => Promise.reject(new Error('not in this test'))
  const app = <RouterProvider router={router} />
  const ui =
    user === undefined ? (
      app
    ) : (
      <AuthContext
        value={{
          status: 'signed-in',
          user,
          features: new Set(['mail']),
          signIn: reject,
          signOut: reject,
          changeTimeZone: reject,
          changeMarkReadSubfolders: reject,
          recheck: vi.fn(),
        }}
      >
        {app}
      </AuthContext>
    )
  render(ui, { wrapper: strict ? StrictMode : undefined })
  return history
}

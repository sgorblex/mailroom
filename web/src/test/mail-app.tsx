import { StrictMode } from 'react'
import { render } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
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

import type { Feature, User } from '@/api/auth.ts'
import { AuthContext } from '@/auth/auth-context.ts'
import { validateFilterDraftSearch } from '@/filters/draft-search.ts'
import { createQueryClient } from '@/lib/query-client.ts'
import { LiveUpdatesProvider } from '@/mail/live-provider.tsx'
import { MailScreen } from '@/mail/mail-screen.tsx'
import { validateMailSearch } from '@/mail/route-search.ts'

/**
 * The mail screen at `path`, in a router of its own, with Accounts and a filter draft as
 * placeholders, under the live updates (a stream only where `stubEventSource` gave one); its
 * history to inspect. `strict` as `main.tsx` renders it; with `user`, signed in
 * as them (else no `AuthProvider`) on an instance with `features`. A cache of its own, never retrying.
 */
export function renderMailApp(
  path: string,
  { strict = false, user, features = ['mail'] }: { strict?: boolean; user?: User; features?: Feature[] } = {},
): RouterHistory {
  const rootRoute = createRootRoute({
    component: () => (
      <LiveUpdatesProvider>
        <Outlet />
      </LiveUpdatesProvider>
    ),
  })
  const accountsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/accounts',
    component: () => <p>Accounts page</p>,
  })
  const filterDraftRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/filters/new',
    validateSearch: validateFilterDraftSearch,
    component: () => <p>Filter draft page</p>,
  })
  const mailRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/mail',
    validateSearch: validateMailSearch,
    component: MailScreen,
  })
  const history = createMemoryHistory({ initialEntries: [path] })
  const router = createRouter({ routeTree: rootRoute.addChildren([accountsRoute, filterDraftRoute, mailRoute]), history })
  // The router restores scroll positions, which jsdom does not implement.
  vi.stubGlobal('scrollTo', vi.fn())
  const reject = (): Promise<void> => Promise.reject(new Error('not in this test'))
  const app = (
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  const ui =
    user === undefined ? (
      app
    ) : (
      <AuthContext
        value={{
          status: 'signed-in',
          user,
          features: new Set(features),
          signIn: reject,
          signOut: reject,
          changeTimeZone: reject,
          changeMarkReadSubfolders: reject,
          changeIndexMaxAge: reject,
          changeConversations: reject,
          recheck: vi.fn(),
        }}
      >
        {app}
      </AuthContext>
    )
  render(ui, { wrapper: strict ? StrictMode : undefined })
  return history
}
